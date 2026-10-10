# GeoLume — Arquitetura atual

## Visão geral

```text
Navegador
   │ HTTP + cookie de sessão + CSRF
   ▼
FastAPI (api.py + worker/web)
   ├── valida autenticação, uploads e autorização
   ├── grava entrada em /saida/inputs
   ├── fluxo síncrono: geolume_worker.job.run_job()
   └── fluxo assíncrono: Redis → Celery → process_job()
                                      │
                                      ▼
                         sessão PyQGIS headless por processo
                                      │
                                      ▼
                    geometria → PDF mapa + PDF memorial + JSON

FastAPI/Celery ───────────────► PostgreSQL/PostGIS
FastAPI/Celery ◄────────────── Redis
```

## Componentes

### API e interface

- `worker/api.py`: FastAPI, autenticação, CSRF, limites de upload, jobs, downloads, camadas e arquivos estáticos.
- `worker/web/`: HTML/CSS/JavaScript sem etapa de build.
- `worker/web/js/`: módulos de API, estado, mapa, camadas, polling, renderização e personalização da prancha.
- `worker/web/vendor/leaflet/`: Leaflet 1.9.4 local.

A API publica somente `127.0.0.1:8000` no Compose de desenvolvimento. `/health` é público; a documentação OpenAPI pública está desativada.

### Processamento geoespacial

- `geolume_worker/input_loader.py`: carrega e valida GeoJSON.
- `geolume_worker/geometry.py`: regras geométricas puras, UTM e tabela de vértices.
- `geolume_worker/processing.py`: processamento e resumo da parcela.
- `geolume_worker/qgis_session.py`: inicialização/finalização controlada do QGIS.
- `geolume_worker/job.py`: contrato do job, métricas e artefatos.
- `geolume_worker/layout.py`: mapa A4 paisagem.
- `geolume_worker/memorial.py`: memorial A4 retrato paginado.
- `geolume_worker/prancha.py`: validação de opções/logo e personalização da prancha.
- `geolume_worker/camadas.py`: catálogo de camadas e estilos.
- `geolume_worker/texto.py`: saneamento e preservação literal de texto.

### Execução assíncrona

`worker/celery_app.py` registra `geolume.process_job`. Redis transporta a tarefa e o resultado. PostgreSQL registra usuário, sessão e estado do job. O processo Celery mantém uma sessão QGIS aberta; `process_job()` usa essa sessão e atualiza o job para `started` (gravando `started_at`), `completed` ou `failed`. As três transições do Celery são UPDATEs condicionais: `db.iniciar_job` (só `queued` vira `started`; recusado, a task não processa nem apaga nada e devolve `inicio_recusado`), `db.falhar_job` (só `started` vira `failed`; recusado, a primeira causa gravada, como `job_expirado`, é mantida e a falha fica só no log; em qualquer falha depois de gravar `started` o worker mantém o GeoJSON, que fica para a pré-visualização mostrar o motivo sobre a geometria e segue a retenção de `failed`, 30 dias, e remove só a logo; se nem o `started` foi gravado, o job continua `queued` e o worker remove GeoJSON e logo) e `db.concluir_job` (só `started` vira `completed`). `run_job` grava o `resultado.json` num temporário da pasta do job com `fsync` e `os.replace`, e faz `fsync` de `mapa.pdf`, `memorial.pdf`, da pasta do job e da pasta de saída antes de devolver; qualquer falha depois de criar a pasta a remove inteira (nenhum artefato parcial). Entre `run_job` e `concluir_job`, a task chama `integridade.conferir_artefatos(OUTPUT_DIR, job_id)`: os 3 artefatos direto em `{saida}/{job_id}/`, abertos sem seguir link (`O_NOFOLLOW`), regulares, com um único hard link, tamanho entre 1 byte e 50 MB; PDF começando com `%PDF-` e com `%%EOF` nos últimos 1024 bytes; `resultado.json` UTF-8, objeto, sem `NaN`/`Infinity`, com o `job_id` do job, `versao_esquema` 1, `pdf_memorial` `memorial.pdf` e `entrada`, `propriedades`, `crs_saida` (`EPSG:n`), `area_ha` e `perimetro_m` finitos e não negativos (>= 0), `vertices` (3 ou mais, com `id` e `azimute` texto não vazio, `e` e `n` finitos e `distancia_m` finita e não negativa) e `metricas` exatamente com `fases_ms` (as 4 fases, finitas e não negativas), `pico_rss_mb`, `rss_antes_mb` e `rss_depois_mb` (finitos e estritamente positivos). Zero é aceito onde o `run_job` arredonda e pode chegar a `0.0` em entrada válida (`area_ha` com 4 casas, `perimetro_m` e `distancia_m` com 2, fases com 0,1 ms; visto gerando pelo QGIS); nunca na memória. Negativo, booleano, `null`, número em texto, `NaN`, infinito e inteiro grande demais para float são inválidos e viram motivo, nunca exceção. A saída só tem `ok` e códigos por artefato, sem caminho nem conteúdo. Reprovado, o job nunca vira `completed`: a task tenta `db.falhar_job` com `artefato_invalido: {artefato}={código}, …` (código interno; a API mostra a falha genérica), mantém artefatos e uploads para diagnóstico e devolve `artefato_invalido` sem caminhos; se o banco recusar (outro processo decidiu), a causa anterior fica e só há aviso no log. Se o banco recusar a conclusão (job já `failed` pela recuperação, já `completed` ou em outro estado), a task registra um aviso, não apaga artefatos nem uploads e devolve `conclusao_recusada` sem caminhos. A API responde só pelo banco, nunca pelo resultado do Celery. Quando o enfileiramento falha depois de o job ser registrado, a API grava `failed` por `db.falhar_enfileiramento` (só a partir de `queued`) e apaga GeoJSON e logo; se o Celery já tiver iniciado ou decidido o job, a API registra um aviso, preserva estado e uploads e responde o mesmo 503.

`worker/jobs_presos.py` tem a regra pura de job preso: `queued` sem `task_id` há mais de 10 min é `enfileiramento_perdido`; `started` com `started_at` há mais de 30 min é `execucao_expirada`. `queued` com `task_id` e `started` sem `started_at` (jobs antigos) não são decididos. `started_at` não aparece na resposta pública.

`worker/recuperacao.py` tem `recuperar_job_expirado(job_id, agora, output_dir)`, ainda sem scheduler nem endpoint que a chame. Aplica a regra; `started` expirado com `mapa.pdf`, `memorial.pdf` e `resultado.json` íntegros (`integridade.conferir_artefatos`) não muda (revisão manual, nunca `completed` automático); artefato zerado, truncado, de outro job ou link nunca conta como completo; nos demais casos marca `failed` com `db.marcar_job_expirado`, um único UPDATE condicionado ao status e ao marco (`created_at`/`started_at`) lidos, e só quem ganhou remove o GeoJSON `inputs/{job_id}-*` e a logo do job. Artefatos parciais ficam. Com os 3 artefatos presentes mas algum inválido, sai só o GeoJSON: a logo fica junto com `mapa.pdf`, `memorial.pdf` e `resultado.json` para diagnóstico. O erro gravado é `job_expirado: …`, que a API mostra como a falha genérica. A leitura sem filtro de usuário (`ler_job`) fica nesse módulo, que a API não importa.

`worker/diagnostico_jobs.py` é o comando manual de diagnóstico (`python3 diagnostico_jobs.py [--job-id ID ...] [--tenant T] [--agora ISO] [--saida DIR]`, no contêiner `api`). Só lê: a sessão do banco é `readonly` e o comando não chama a recuperação nem toca em arquivos. Sem `--job-id`, lê só jobs `queued` e `started`. Imprime JSON ordenado com, por job, `categoria` (`candidato_recuperacao`, `artefatos_presentes`, `nao_expirado`, `nao_recuperavel`, `nao_encontrado`), `motivo`, marco e idade usados na regra, presença dos 3 artefatos e dos uploads (GeoJSON e logo) e `tenant_id`; `artefatos.estado` é `completos` só com os 3 íntegros, `invalidos` com os 3 presentes mas algum reprovado (o job continua `candidato_recuperacao`, nunca `artefatos_presentes`), `parciais` ou `ausentes`, e `artefatos.motivos` traz os códigos de `conferir_artefatos`; não inclui caminhos, `owner_id`, prancha nem erro. Com `--tenant`, outro tenant aparece como `nao_encontrado`. A API não importa o módulo.

`worker/recuperar_job.py` é a interface manual da recuperação. Sem `--apply`, só imprime o diagnóstico (somente leitura). A escrita exige `--apply`, exatamente um `--job-id` e `--confirm-job-id` igual ao id; `--agora` é recusado com `--apply` (a recuperação usa o relógio real). Não existe recuperação global. Com `--tenant`, job de outro tenant é `nao_encontrado` e a recuperação nem é chamada. Aplicado, chama `recuperar_job_expirado`, que relê o job e só marca `failed` pelo UPDATE condicional; a saída JSON traz `resultado` (`marcado_failed`, `nao_encontrado`, `nao_expirado`, `artefatos_presentes`, `outro_processo`, `confirmacao_invalida`, `argumento_invalido`) e o diagnóstico antes e depois; se a releitura falhar depois de `marcado_failed`, `depois` é `null` e `erro_diagnostico` traz só o tipo do erro, sem repetir a escrita. Saída 0 para diagnóstico ou job recuperado, 1 para recuperação recusada, 2 para pedido inválido.

`worker/diagnostico_fila.py` é o diagnóstico somente leitura de jobs `queued` no broker (`python3 diagnostico_fila.py [--job-id ID ...] [--tenant T] [--agora ISO]`, no contêiner `api`). Lê o banco em sessão `readonly`; no Redis usa só `LRANGE` nas filas `celery` e de prioridade (`celery3/6/9`) e `HVALS` em `unacked`, juntos num `MULTI/EXEC`, e `GET` em `celery-task-meta-{task_id}` (só o `status`). Não usa `celery inspect`/controle, que publica mensagens no broker. Categorias: `fila_confirmada` (mensagem na fila ou reservada por um worker), `task_nao_localizada` (fila e reservas lidas por inteiro, sem a mensagem, e o backend sem estado de execução; traz `estado_resultado`, ex. `FAILURE`), `task_id_invalido` (fora do formato textual de UUID em minúsculas, `8-4-4-4-12` hexadecimal; a versão do UUID não é conferida — o Celery gera `str(uuid4())`, que sempre passa), `queued_sem_task_id` (com o motivo de `motivo_job_preso`), `nao_verificavel` (`broker_indisponivel`, `resultado_indisponivel`, `resultado_ilegivel`, `mensagem_ilegivel_no_broker`, `task_em_execucao_no_backend` ou `estado_mudou_durante_a_consulta`), `nao_aplicavel` (não está `queued`) e `nao_encontrado` (inexistente ou de outro tenant). O job é relido depois do broker; se mudou de status ou de `task_id`, vira `nao_verificavel`. `task_nao_localizada` é observação, não prova de abandono: o diagnóstico não marca, não apaga e não chama a recuperação. A saída não traz `task_id`, caminhos, `owner_id` nem erro. A API e o Celery não importam o módulo. Os testes que publicam no Redis só usam `worker/tests/broker_isolado.py`: URLs efetivas de escrita, leitura e resultado conferidas contra a DB 15 antes de devolver o app; uso exclusivo da DB 15 por trava Redis (`geolume-itest:trava-db15`, 120 s); DB ocupada ou suja falha o teste (nunca skip) sem apagar nada; no fim, com a posse conferida e renovada, apaga só as chaves que o teste registrou antes de criar, pelo nome; trava vencida não apaga nada; erros de limpeza ou liberação nunca substituem a falha original do teste.

`worker/retencao.py` é o plano de retenção de arquivos, só dry-run (`python3 retencao.py [--job-id ID ...] [--tenant T] [--agora ISO] [--saida DIR]`, no contêiner `api`). Lê os jobs numa sessão `readonly` e o disco com `lstat` e imprime JSON ordenado; não existe `--apply` nem código de exclusão. Períodos contados de `completed_at`: `completed` até 90 dias é `manter` (downloads, entrada do histórico e logo); depois é só `candidato_retencao`, sem limpeza. `failed` até 30 dias é `manter` (diagnóstico); depois é `candidato_limpeza`, mas só o GeoJSON e a logo do próprio job são candidatos; artefatos parciais e PDFs ficam. `queued` e `started` (com ou sem `task_id`), status desconhecido, data ausente, sem fuso ou futura: `manter`. Um arquivo só é candidato se for `inputs/{job_id}-{input_filename}` (`.geojson`) ou `logos/{job_id}.png`, regular, com um único hard link e com `job_id` válido. Proteção contra link, toda por `lstat`: o próprio arquivo; a pasta direta (`inputs/`, `logos/` ou `{job_id}/`); e `--saida` mais cada pasta acima dele até a raiz, no caminho absoluto sem normalizar `..` (um componente link ou um `lstat` negado em qualquer um deles deixa todos os arquivos do job como `manter`). Nada além disso é conferido: não há abertura com `O_NOFOLLOW` nem proteção contra troca do arquivo entre o plano e uma exclusão futura, que precisará revalidar na hora. Fora dessas regras o arquivo é mantido com o motivo (`fora_da_pasta_permitida`, `link_recusado`, `hard_link_recusado`, `nao_e_arquivo_regular`, `nao_verificavel` quando o `lstat` falha, `job_id_invalido`). A saída não traz caminhos, `owner_id`, prancha nem erro; com `--tenant`, outro tenant aparece como `nao_encontrado`. Ids repetidos aparecem uma vez; `resumo` conta as quatro decisões (`manter`, `candidato_retencao`, `candidato_limpeza`, `nao_encontrado`) e soma `total`. A API não importa o módulo.

### Persistência e arquivos

- Volume PostgreSQL: `geolume-postgis`.
- `/saida/inputs/`: entradas de uploads assíncronos.
- `/saida/<job_id>/` e estruturas de jobs existentes: artefatos gerados.
- Artefatos públicos do job concluído: `mapa.pdf`, `memorial.pdf`, `resultado.json`.
- A API resolve e valida caminhos antes de servir arquivos; caminhos internos não são retornados ao cliente.

## Contrato de entrada atual

- GeoJSON com exatamente um polígono simples.
- EPSG:4326 ou EPSG:4674. Membro `crs` legado só com nome EPSG/URN aceito; qualquer outro (inclusive `link`) é recusado antes do OGR, que cairia em EPSG:4326 sem avisar ou baixaria o `href`.
- Sem furos, sem múltiplas feições e sem múltiplas partes.
- Limite de 10 MB e até 5.000 vértices.
- Cobertura UTM SIRGAS 2000 suportada: fusos 17N–22N e 18S–25S, conferida em cada vértice (não só no centroide). Latitude fora de -90°..90° ou longitude fora de -180°..180° sai como `coordenada_invalida`. Polígono válido em graus pode ficar sem vértices suficientes: depois da reprojeção para UTM, `geometry.vertex_table` arredonda E e N a três casas decimais e deduplica os pontos consecutivos que ficam iguais depois do arredondamento, além do ponto de fechamento igual ao primeiro; se restarem menos de 3 vértices, `processing.process` recusa com `poligono_muito_pequeno` (código público), antes de criar a pasta do job. Não é uma regra de distância: pontos a menos de 1 mm que arredondam para valores diferentes continuam distintos, e pontos não consecutivos não são comparados.
- Upload assíncrono total: 12 MB, incluindo logo de até 2 MB.

## Contratos de saída

`resultado.json` inclui versão do esquema, identificação da entrada/job, propriedades, EPSG de saída, área, perímetro, vértices e métricas. O mapa é A4 paisagem; o memorial é A4 retrato, pode ser paginado e contém rodapé indicando que é preliminar.

Regressão coberta em `worker/tests/test_contrato_artefatos.py`, com a gleba fictícia `gleba_rural_exemplo.geojson` gerada pelo QGIS em pasta temporária: texto do `memorial.pdf` (`pdftotext -layout`) igual a `fixtures/memorial_gleba_6v.txt`; `resultado.json` igual a `fixtures/resultado_gleba_6v.json` sem `job_id` e sem os valores de `metricas` (tempo e memória variam); contrato exato do JSON (as 10 chaves na ordem, tipos, `versao_esquema` 1, `pdf_memorial` `memorial.pdf`, `crs_saida` `EPSG:n`, vértices com `id`, `e`, `n`, `azimute`, `distancia_m` e `metricas` com as 4 fases), com chave ausente, chave a mais e tipo trocado recusados; e opções de prancha (projeto, responsável, legenda, estilo, cores, opacidade, logo) sem efeito no memorial nem no JSON. Mudança intencional no memorial ou no JSON exige regenerar a referência e revisar o diff.

## Segurança relevante

- Sessão opaca em cookie `HttpOnly`, `Secure`, `SameSite=Lax`; somente hash do token no banco.
- Senhas com hash scrypt e bloqueio após tentativas inválidas.
- Todo POST exige `X-GeoLume-CSRF: 1` e validação de `Origin` quando presente.
- Jobs e artefatos respeitam usuário/tenant; inexistente e não autorizado respondem 404. Coberto contra o PostgreSQL real (schema `itest_*`, `JOB_ACCESS` de verdade): dono e administrador do mesmo tenant acessam status, os 3 downloads, GeoJSON e logo; colega do mesmo tenant, membro de outro tenant com o mesmo `user_id` e administrador de outro tenant recebem 404 nos 6 recursos, com a mesma resposta de um id inexistente; a listagem só traz o próprio tenant.
- Uploads são limitados antes e durante a leitura do multipart.
- Logos são validadas pelo conteúdo, normalizadas para PNG e recebem nome derivado do job.
- Arquivos são abertos com validações contra links simbólicos/hard links.
- `GET /jobs/{task_id}/input` só serve o upload do próprio job: `input_path` precisa ser `{job_id}-{nome}.geojson` direto em `inputs/`, com o `id` do job autorizado, sem link simbólico no nome e com o caminho resolvido igual ao esperado; `input_path` de outro job (mesmo tenant ou não), sem o prefixo, em subpasta ou fora de `inputs/` responde o mesmo 404 genérico.

## Verificação por camada

| Camada | Verificação |
|---|---|
| Geometria pura | `docker compose run --rm worker` / pytest |
| PyQGIS e PDF | worker dentro do Docker + inspeção textual/visual |
| API/banco | stack `api redis postgis celery` + testes de integração |
| Frontend | `npm run test:unit` e `npm run test:e2e` em localhost |

## Limitações atuais

O fluxo é orientado a um polígono GeoJSON. KML/Shapefile, dados de clientes, autenticação expandida, concorrência maior, APIs comerciais de mapas/satélite e produção são etapas posteriores, não devem ser presumidos como implementados.

Limitações conhecidas, verificadas no QA de 2026-10-09:

- `acks_late` está desligado: parar o Celery com a fila parada preserva as mensagens (job `queued` com `task_id` é processado ao voltar), mas parar durante um processamento perde a mensagem e deixa o job `started` até expirar (30 min) e ser recuperado à mão. Reiniciar o Celery só sem job `started`.
- 7 jobs `completed` de teste de 2026-10-01 têm artefatos zerados e continuam sendo servidos; a conferência de integridade vale para jobs novos.

O MVP atual deve continuar sem dependência de APIs comerciais que gerem cobrança. OSM pode ser usado apenas dentro da política aplicável para pré-visualização; o `mapa.pdf` deve continuar gerado sem depender de rede ou de imagens externas não licenciadas. A integração de satélite, mapas comerciais e fontes oficiais remotas deve ser planejada depois de apoio/financiamento e de uma decisão documentada sobre licença, custo, cache e uso comercial.

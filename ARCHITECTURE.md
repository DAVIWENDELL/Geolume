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

`worker/celery_app.py` registra `geolume.process_job`. Redis transporta a tarefa e o resultado. PostgreSQL registra usuário, sessão e estado do job. O processo Celery mantém uma sessão QGIS aberta; `process_job()` usa essa sessão e atualiza o job para `started` (gravando `started_at`), `completed` ou `failed`. As três transições do Celery são UPDATEs condicionais: `db.iniciar_job` (só `queued` vira `started`; recusado, a task não processa nem apaga nada e devolve `inicio_recusado`), `db.falhar_job` (só `started` vira `failed`; recusado, a primeira causa gravada, como `job_expirado`, é mantida e a falha fica só no log) e `db.concluir_job` (só `started` vira `completed`). Se o banco recusar a conclusão (job já `failed` pela recuperação, já `completed` ou em outro estado), a task registra um aviso, não apaga artefatos nem uploads e devolve `conclusao_recusada` sem caminhos. A API responde só pelo banco, nunca pelo resultado do Celery. Quando o enfileiramento falha depois de o job ser registrado, a API grava `failed` por `db.falhar_enfileiramento` (só a partir de `queued`) e apaga GeoJSON e logo; se o Celery já tiver iniciado ou decidido o job, a API registra um aviso, preserva estado e uploads e responde o mesmo 503.

`worker/jobs_presos.py` tem a regra pura de job preso: `queued` sem `task_id` há mais de 10 min é `enfileiramento_perdido`; `started` com `started_at` há mais de 30 min é `execucao_expirada`. `queued` com `task_id` e `started` sem `started_at` (jobs antigos) não são decididos. `started_at` não aparece na resposta pública.

`worker/recuperacao.py` tem `recuperar_job_expirado(job_id, agora, output_dir)`, ainda sem scheduler nem endpoint que a chame. Aplica a regra; `started` expirado com `mapa.pdf`, `memorial.pdf` e `resultado.json` presentes não muda (revisão manual, nunca `completed` automático); nos demais casos marca `failed` com `db.marcar_job_expirado`, um único UPDATE condicionado ao status e ao marco (`created_at`/`started_at`) lidos, e só quem ganhou remove o GeoJSON `inputs/{job_id}-*` e a logo do job. Artefatos parciais ficam. O erro gravado é `job_expirado: …`, que a API mostra como a falha genérica. A leitura sem filtro de usuário (`ler_job`) fica nesse módulo, que a API não importa.

`worker/diagnostico_jobs.py` é o comando manual de diagnóstico (`python3 diagnostico_jobs.py [--job-id ID ...] [--tenant T] [--agora ISO] [--saida DIR]`, no contêiner `api`). Só lê: a sessão do banco é `readonly` e o comando não chama a recuperação nem toca em arquivos. Sem `--job-id`, lê só jobs `queued` e `started`. Imprime JSON ordenado com, por job, `categoria` (`candidato_recuperacao`, `artefatos_presentes`, `nao_expirado`, `nao_recuperavel`, `nao_encontrado`), `motivo`, marco e idade usados na regra, presença dos 3 artefatos e dos uploads (GeoJSON e logo) e `tenant_id`; não inclui caminhos, `owner_id`, prancha nem erro. Com `--tenant`, outro tenant aparece como `nao_encontrado`. A API não importa o módulo.

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
- Cobertura UTM SIRGAS 2000 suportada: fusos 17N–22N e 18S–25S, conferida em cada vértice (não só no centroide). Latitude fora de -90°..90° ou longitude fora de -180°..180° sai como `coordenada_invalida`.
- Upload assíncrono total: 12 MB, incluindo logo de até 2 MB.

## Contratos de saída

`resultado.json` inclui versão do esquema, identificação da entrada/job, propriedades, EPSG de saída, área, perímetro, vértices e métricas. O mapa é A4 paisagem; o memorial é A4 retrato, pode ser paginado e contém rodapé indicando que é preliminar.

## Segurança relevante

- Sessão opaca em cookie `HttpOnly`, `Secure`, `SameSite=Lax`; somente hash do token no banco.
- Senhas com hash scrypt e bloqueio após tentativas inválidas.
- Todo POST exige `X-GeoLume-CSRF: 1` e validação de `Origin` quando presente.
- Jobs e artefatos respeitam usuário/tenant; inexistente e não autorizado respondem 404.
- Uploads são limitados antes e durante a leitura do multipart.
- Logos são validadas pelo conteúdo, normalizadas para PNG e recebem nome derivado do job.
- Arquivos são abertos com validações contra links simbólicos/hard links.

## Verificação por camada

| Camada | Verificação |
|---|---|
| Geometria pura | `docker compose run --rm worker` / pytest |
| PyQGIS e PDF | worker dentro do Docker + inspeção textual/visual |
| API/banco | stack `api redis postgis celery` + testes de integração |
| Frontend | `npm run test:unit` e `npm run test:e2e` em localhost |

## Limitações atuais

O fluxo é orientado a um polígono GeoJSON. KML/Shapefile, dados de clientes, autenticação expandida, concorrência maior, APIs comerciais de mapas/satélite e produção são etapas posteriores, não devem ser presumidos como implementados.

O MVP atual deve continuar sem dependência de APIs comerciais que gerem cobrança. OSM pode ser usado apenas dentro da política aplicável para pré-visualização; o `mapa.pdf` deve continuar gerado sem depender de rede ou de imagens externas não licenciadas. A integração de satélite, mapas comerciais e fontes oficiais remotas deve ser planejada depois de apoio/financiamento e de uma decisão documentada sobre licença, custo, cache e uso comercial.

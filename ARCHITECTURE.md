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

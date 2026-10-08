# GeoLume — Memória operacional do projeto

## Decisões confirmadas

- GeoLume é uma experiência web SaaS; QGIS permanece como motor interno headless.
- A primeira entrada suportada é um GeoJSON com um polígono simples.
- O worker gera mapa, memorial preliminar e JSON técnico.
- O fluxo síncrono permanece útil para depuração; o assíncrono usa Redis/Celery.
- PostgreSQL/PostGIS é a persistência local do projeto.
- Celery usa `--pool=solo` enquanto a segurança de QGIS em outros pools não for demonstrada.
- O mapa-base OSM é adequado para desenvolvimento, mas não deve ser tratado como provedor de produção em escala sem verificar a política/licença.
- O memorial deve continuar identificado como preliminar até que requisitos profissionais e legais sejam definidos.
- 2026-10-01 — Catálogo de camadas definido só no servidor (`geolume_worker/camadas.py`, servido por `GET /camadas` com sessão). Motivo: uma fonte única para tela e PDF. Camadas sem fonte licenciada (satélite, limites, hidrografia, rodovias) ficam desabilitadas e sem URL até haver provedor confirmado.
- 2026-10-01 — O `mapa.pdf` não contém camada raster nem acessa a rede; OSM é só pré-visualização. Verificado gerando PDFs em container `--network none`.
- 2026-10-01 — Alfa do preenchimento: `alfa8 = floor(alfa × 255 + 0,5)` (metade para cima) no Python e no JavaScript. O `round()` do Python foi rejeitado porque arredonda ao par e faria tela e PDF divergirem em 30 % e 70 %. Alfa inválido é recusado, nunca corrigido.
- 2026-10-01 — Jobs antigos sem `estilo`/`alfa_preenchimento` valem `padrao`/0.35, sem migração de banco.
- 2026-10-01 — O frontend não tem mais lista local de mapas-base nem fallback de catálogo: sem catálogo válido, `withBase` não troca o mapa-base e a legenda mostra "Mapa-base: nenhum". O polígono muda só por `mapView.setStyle` (prancha completa). Não reintroduzir lista fixa no navegador.
- 2026-10-05 — Quadro de coordenadas do `mapa.pdf`: o ponto de referência é o centroide calculado no plano UTM e levado a SIRGAS 2000 geográficas (EPSG:4674), para que GMS e E/N mostrem o mesmo ponto. GMS com segundos em centésimos arredondados metade para cima sobre inteiros (59,995" sobe o minuto; nunca 60"), hemisférios S/N e L/O, separador decimal ponto como o resto do PDF. A fonte é declarada só como "GeoJSON fornecido pelo usuário", nunca como dado oficial ou cadastral. `centroide_geo` existe só em `ParcelSummary`; `resultado.json` não mudou.
- 2026-10-06 — Marca de autoria no `mapa.pdf`: todo mapa traz o símbolo oficial local do GeoLume (`worker/web/assets/geolume-marca-transparente.png`, o mesmo arquivo da tela) e o texto "Gerado pelo GeoLume", no rodapé da coluna direita, alinhados à base do quadro de fontes. É identificação visual de procedência do sistema, não selo técnico, certificação, aprovação, CREA nem garantia de precisão; o PDF não deve ganhar textos desse tipo. Independe da logo enviada pelo cliente (canto superior direito). Alternativas rejeitadas: selo técnico ou marca inventada sem arquivo autorizado; cópia reduzida da logo (o arquivo oficial é embutido inteiro e o `mapa.pdf` passou de ~24 KB para ~234 KB, aceito no MVP). Coberto por `worker/tests/test_layout_autoria.py`; API, prancha, `memorial.pdf` e `resultado.json` não mudaram.
- 2026-10-08 — Validação de coordenadas: o OGR, ao ler um GeoJSON com membro `crs` que não reconhece, cai em EPSG:4326 sem avisar e, para `"type": "link"`, tenta baixar o `href` (requisição de saída controlada pelo cliente). Por isso o `crs` declarado é conferido no JSON bruto antes de abrir o OGR; só EPSG:4326/4674 (nome ou URN) e a URN CRS84 1.3 passam. A cobertura UTM é conferida em cada vértice; o centroide continua escolhendo o fuso. Código novo `coordenada_invalida` para latitude/longitude impossíveis. `OGC:CRS84` segue recusado, como antes.
- 2026-10-08 — A API roda uvicorn sem `--reload` e o Celery carrega o código só ao iniciar: depois de mudar `worker/` (inclusive `CODIGOS_DE_VALIDACAO`), reinicie `api` e `celery` (`docker compose restart api celery`). Visto na validação do fluxo assíncrono: a API antiga mostrava "Falha no processamento do job." para um `coordenada_invalida` já gravado corretamente no banco.
- 2026-10-08 — Job preso: `created_at` não mede execução (há jobs reais concluídos 19 h depois de criados, por espera na fila com o Celery parado), por isso existe `started_at`, gravado por `update_job(..., "started")`, sem backfill nos antigos. A regra (`worker/jobs_presos.py`) só decide com critério verificável no registro: `queued` sem `task_id` > 10 min e `started` com `started_at` > 30 min. `queued` com `task_id` fica indeciso porque a mensagem pode estar no Redis. A recuperação automática futura não pode marcar `failed` sem antes conferir os artefatos (`resultado.json` é gravado por último) e precisa de UPDATE condicional ao status/`started_at` lidos.
- 2026-10-08 — Recuperação de job expirado: `db` não tem leitura de job sem filtro de usuário (guarda em `test_api_authz.py`, para a API nunca fazer consulta global); a leitura da recuperação (`ler_job`) fica em `worker/recuperacao.py`, que a API não importa (guarda em `test_recuperacao.py`). Testes de corrida rodam com `GEOLUME_DB_INTEGRATION=1` no contêiner `api`, num schema `itest_*` criado e apagado pelo teste, nunca em `public.jobs`. Um `started` expirado com os 3 artefatos não é tocado: a promoção a `completed` exige conferir integridade, ainda não definida.
- 2026-10-08 — Conclusão condicional do Celery: só `started` vira `completed` (`db.concluir_job`). Recusada, a task não marca `failed` nem apaga nada — o estado oficial é o do banco, e os PDFs gerados ficam no disco sem rota de download, porque a API só serve arquivos de job `completed`. Banco fora ao concluir propaga a exceção sem limpar: o job fica `started` com os 3 artefatos, caso que a recuperação devolve como `artefatos_presentes`.
- 2026-10-08 — O Celery não usa mais `update_job`: as três transições são condicionais (`iniciar_job` só de `queued`, `falhar_job` e `concluir_job` só de `started`), então a primeira decisão gravada no banco vale e uma mensagem atrasada ou reentregue não ressuscita nem reprocessa job. Uma falha recusada continua propagando a exceção (o Celery registra a tarefa como falha), mas o `erro` gravado é o primeiro. A API também não: o `failed` ao falhar o enfileiramento é `falhar_enfileiramento` (só de `queued`), porque `delay()` pode ter entregue a mensagem antes de `set_task_id` falhar e o Celery já ter iniciado o job; recusado, a API preserva estado e uploads. `update_job` ficou só para testes antigos de integração.
- 2026-10-08 — Diagnóstico de jobs presos (`worker/diagnostico_jobs.py`) é separado da recuperação e só lê: sessão psycopg2 `readonly` (escrita falha com `ReadOnlySqlTransaction`, testado em Postgres real), não chama `db.marcar_job_expirado` nem `recuperar_job_expirado` (só reaproveita `ARTEFATOS`). Fica fora de `db.py` pelo mesmo motivo de `ler_job`: a API não pode ter leitura global. `artefatos_presentes` segue a mesma regra da recuperação (só `started` expirado com os 3 arquivos).
- 2026-10-08 — Recuperação manual (`worker/recuperar_job.py`): `--apply` recusa `--agora` de propósito, porque um instante no futuro forjaria a expiração de qualquer job; a decisão usa sempre o relógio real. O tenant é conferido na leitura do diagnóstico antes de chamar a recuperação (o UPDATE condicional não filtra tenant, que não muda depois de criado o job). Se a releitura depois de um `marcado_failed` falhar, a saída continua JSON com `resultado: marcado_failed`, `depois: null` e `erro_diagnostico` (só o tipo da exceção, sem mensagem nem traceback), código 0 e nenhuma nova escrita: a alteração já gravada nunca fica escondida.
- 2026-10-08 — Job `abc`, único candidato apontado pelo diagnóstico somente leitura (`diagnostico_jobs.py`): o usuário executou e verificou a recuperação manual em 2026-10-08 (`completed_at` 15:39 BRT), antes de existir `recuperar_job.py`; portanto não foi feita com `--apply` deste CLI. Resultado verificado: `queued` → `failed` com `job_expirado: O job não chegou à fila de processamento.`, diagnóstico depois sem o `abc` como candidato, nenhum arquivo apagado, nenhum outro job alterado, jobs E2E preservados, sem recuperação global. O `recuperar_job.py` nunca foi executado com `--apply` contra jobs reais.

## Fatos verificados no repositório

- A interface é HTML/CSS/JavaScript puro servido pelo FastAPI.
- Há autenticação por sessão, autorização por job, CSRF, limites de upload e sanitização de arquivos.
- Há personalização de prancha: projeto, responsável, logo, cores e layout.
- Há pré-visualização Leaflet e catálogo de camadas.
- Testes de unidade, API, worker e E2E estão versionados.
- O Git do projeto estava limpo no início desta documentação.

## Contexto do AI Brain

A memória compartilhada foi consultada antes da documentação. Ela contém notas históricas sobre identidade, arquitetura planejada e estado do MVP. Algumas notas antigas descrevem o projeto como ainda não implementado; o código atual comprova que o MVP já possui API, Celery, Redis, PostgreSQL, interface e testes. Por isso, código e testes são o estado atual; a memória deve continuar sendo atualizada com fatos aprovados, sem substituir a inspeção do repositório.

## Pendências conhecidas

- Existem referências históricas a jobs que ficaram presos em `started` durante uma falha anterior. Em 2026-10-08 o banco local não tinha nenhum `started`; o único preso era `abc` (`queued`, sem `task_id`).
- Ainda faltam validação com clientes, arquivos reais maiores, concorrência e definição de requisitos profissionais do memorial.
- KML, Shapefile, clientes, imóveis, multi-tenancy de produção e billing não devem ser tratados como implementados.

## Registro de decisões futuras

Ao tomar uma decisão que afete produto, contrato de API, segurança, formato de arquivo, PDF ou infraestrutura, adicionar:

- data;
- decisão;
- motivo;
- alternativas rejeitadas;
- impacto e testes necessários.

Não registrar senhas, tokens, dados pessoais de clientes ou conteúdo bruto de arquivos enviados.

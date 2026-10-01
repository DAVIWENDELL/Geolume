# PoC do Worker PyQGIS em Docker — Plano de Implementação

> **Para agentes executores:** SUB-SKILL OBRIGATÓRIA: use superpowers:subagent-driven-development (recomendado) ou superpowers:executing-plans para implementar este plano tarefa por tarefa. Os passos usam checkbox (`- [ ]`) para acompanhamento.

**Objetivo:** Um container Docker que recebe um polígono GeoJSON, processa com PyQGIS headless, gera um PDF de mapa + um JSON com vértices/área/perímetro, e mede tempo por fase e consumo de memória.

**Arquitetura:** Pacote Python `geolume_worker` com uma função pura de entrada `run_job(input_path, output_dir) -> JobResult`, independente de CLI. A inicialização do QGIS fica isolada num gerenciador de contexto reutilizável (`qgis_session`), para que um futuro worker Celery inicialize o QGIS uma vez por processo e execute vários jobs. A CLI e o script de benchmark são apenas casca fina sobre `run_job`.

**Stack:** imagem oficial `qgis/qgis:3.44.14-noble` (QGIS 3.44 LTR, Ubuntu 24.04, Python 3.12 do sistema), PyQGIS, pytest (via apt), poppler-utils (verificação de texto do PDF), Docker Compose.

**Spec:** não há documento de spec no repositório. Fonte: AI Brain — notas "GeoLume — identidade e status" e "GeoLume — arquitetura planejada e próximo passo" (2026-09-24). As decisões marcadas **[PADRÃO]** abaixo foram tomadas neste plano e precisam de confirmação do usuário antes da execução.

## Global Constraints

- Nenhum código existe; não presumir funcionalidades, integrações ou infraestrutura existentes.
- Meta de produto: geração em até 60 segundos, PDF de alta qualidade. A PoC **mede** contra essa meta; não reprova automaticamente.
- Stack alvo futura: Python, FastAPI, PyQGIS headless, PostgreSQL/PostGIS, Redis, Celery, containers. Nesta PoC: **sem** FastAPI, Celery, Redis ou banco — apenas a base de interface para eles.
- Imagem base fixada em `qgis/qgis:3.44.14-noble` (nunca `latest`, `ltr` ou `stable` sem patch).
- Pytest instalado via `apt` (`python3-pytest`), não via `pip` (Ubuntu 24.04 bloqueia pip no Python do sistema — PEP 668).
- Nenhum acesso à internet durante o processamento (sem mapa de fundo remoto).
- Mensagens de erro e chaves de JSON de saída em português, sem acentos nas chaves.
- Ambiente do desenvolvedor: Windows 11 + Docker Desktop (WSL2). Comandos de uso documentados para PowerShell. Arquivos de texto com fim de linha LF.
- Commits, `git init`, push e qualquer ação destrutiva só com autorização específica do usuário. Os passos "Checkpoint" abaixo indicam onde commitar **se autorizado**.

## Decisões

| # | Decisão | Valor | Status |
|---|---------|-------|--------|
| D1 | Imagem base | `qgis/qgis:3.44.14-noble` (LTR atual, SO LTS) | [PADRÃO] |
| D2 | Formato de entrada | GeoJSON com exatamente 1 feição Polygon (ou MultiPolygon de 1 parte), sem furos, em EPSG:4326 ou EPSG:4674 | [PADRÃO] — depende do formato real do cliente |
| D3 | CRS de saída | SIRGAS 2000 / UTM, fuso calculado pelo centroide. Sul: `EPSG = 31960 + fuso` (fusos 18–25 → 31978–31985). Norte: `EPSG = 31955 + fuso` (fusos 17–22 → 31972–31977). Fora disso → erro `fora_da_cobertura` | [PADRÃO] |
| D4 | Processamento | Reprojetar; calcular área (ha), perímetro (m), tabela de vértices V1..Vn com E/N, azimute de quadrícula (DMS) e distância até o próximo vértice | [PADRÃO] |
| D5 | Artefatos de saída | `mapa.pdf` (A4 paisagem, título "GeoLume — Mapa de Localização", polígono, barra de escala, seta de norte, texto com EPSG e área) + `resultado.json` | [PADRÃO] — layout real depende do modelo do cliente |
| D6 | Mapa de fundo | Nenhum na PoC | [PADRÃO] |
| D7 | Memorial descritivo | Fora da PoC; `resultado.json` já contém os dados que o memorial usará | [PADRÃO] |
| D8 | Medição de tempo | `time.perf_counter` por fase: `inicializacao_qgis`, `carregar`, `processar`, `renderizar_pdf`, `gravar_json`, `total_job` | [PADRÃO] |
| D9 | Medição de memória | pico do processo (`resource.getrusage(...).ru_maxrss`), RSS atual (`/proc/self/status` `VmRSS`), pico do container (`/sys/fs/cgroup/memory.peak`, cgroup v2) | [PADRÃO] |
| D10 | Limite de memória no compose | `mem_limit: 2g` para a medição | [PADRÃO] — ajustar conforme máquina-alvo |
| D11 | Limites de entrada | arquivo ≤ 10 MB; ≤ 5 000 vértices | [PADRÃO] |
| D12 | Base para Celery | `qgis_session()` reentrante (1 init por processo) + `run_job` sem estado global além do QGIS | Firme |

## Riscos

| Risco | Impacto | Mitigação no plano |
|-------|---------|--------------------|
| Imagem QGIS grande (~2–4 GB), pull lento | Atrasa início | Task 1 faz o pull uma vez; tag fixada |
| Renderização headless sem display | PDF não gera ou trava | `QT_QPA_PLATFORM=offscreen` no Dockerfile; teste do PDF na Task 6 |
| Fontes ausentes → PDF com texto em branco | PDF "gera" mas sem texto | `fonts-dejavu-core` na imagem; teste com `pdftotext` exige o título no PDF |
| `exitQgis()` com segfault ou vazamento entre jobs | Worker Celery instável | Teste de 2 jobs na mesma sessão (Task 7); benchmark mede RSS ao longo de N jobs; futuro: `worker_max_tasks_per_child` |
| `ru_maxrss` é pico da vida do processo, não por job | Métrica enganosa | Registrar também `VmRSS` antes/depois de cada job e pico do cgroup |
| Docker Desktop (VM WSL2) ≠ produção | Números não transferíveis | Registrar CPU/RAM da VM no resultado; repetir depois na máquina-alvo |
| CRLF vindo do Windows quebra scripts no container | Falhas estranhas | `.gitattributes` com `eol=lf` |
| Bind mount do Windows lento | Distorce tempo de I/O | Saída gravada em volume; tempo de I/O isolado na fase `gravar_json`/`renderizar_pdf` |
| Formato real de entrada/layout do cliente é diferente | Retrabalho | Isolar em `input_loader.py` e `layout.py`; D2/D5 marcados como padrão |
| Polígono cruzando fusos UTM | Distorção pequena | Aceito na PoC; fuso pelo centroide |

---

## Estrutura de arquivos

```
docker-compose.yml                 # serviço "worker", volumes, mem_limit
.gitattributes                     # * text=auto eol=lf
worker/
  Dockerfile
  .dockerignore
  pyproject.toml                   # metadados do pacote + config do pytest
  README.md                        # comandos PowerShell de uso e medição
  geolume_worker/
    __init__.py
    __main__.py                    # CLI: python -m geolume_worker run ...
    errors.py                      # InvalidInputError(codigo, mensagem)
    geometry.py                    # funções puras: fuso/EPSG, azimute, DMS, tabela de vértices
    metrics.py                     # PhaseTimer, peak_rss_mb, current_rss_mb, cgroup_memory_peak_mb
    qgis_session.py                # qgis_session() reentrante
    input_loader.py                # valida arquivo e devolve QgsVectorLayer + geometria
    processing.py                  # reprojeção + ParcelSummary
    layout.py                      # export_map_pdf(...)
    job.py                         # run_job(...) -> JobResult, grava resultado.json
  scripts/
    benchmark.py                   # N jobs quentes + estatísticas
  tests/
    conftest.py                    # fixture de sessão QGIS + caminhos de fixtures
    fixtures/
      lote_simples.geojson         # quadrado 0,001° × 0,001° com canto SW em (-47.9000, -15.8000)
      lote_boa_vista.geojson       # mesmo quadrado com canto SW em (-60.7000, 2.8000)
      multiplas_feicoes.geojson    # 2 polígonos
      linha.geojson                # LineString
      autointersecao.geojson       # "gravata" (bowtie)
      com_furo.geojson             # polígono com anel interno
      vazio.geojson                # FeatureCollection sem feições
      invalido.json                # texto que não é JSON
      europa.geojson               # quadrado em (10.0, 50.0)
    test_smoke.py
    test_geometry.py
    test_metrics.py
    test_input_loader.py
    test_processing.py
    test_layout.py
    test_job.py
    test_cli.py
    test_benchmark.py
```

---

### Task 1: Scaffold e imagem Docker funcionando

**Arquivos:**
- Criar: `docker-compose.yml`, `.gitattributes`, `worker/Dockerfile`, `worker/.dockerignore`, `worker/pyproject.toml`, `worker/geolume_worker/__init__.py`
- Teste: `worker/tests/test_smoke.py`

**Interfaces:**
- Produz: serviço compose `worker`, com `/app` = `./worker` e `/saida` = `./saida` montados; comando de teste `docker compose run --rm worker pytest`.

Conteúdo fixado:
- `Dockerfile`: `FROM qgis/qgis:3.44.14-noble`; `ENV QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app`; `apt-get install -y --no-install-recommends python3-pytest poppler-utils fonts-dejavu-core`; `WORKDIR /app`; `COPY . /app`.
- `docker-compose.yml`: serviço `worker`, `build: ./worker`, `volumes: ["./worker:/app", "./saida:/saida"]`, `mem_limit: 2g`.
- `pyproject.toml`: `[tool.pytest.ini_options] testpaths = ["tests"]`.

- [ ] **Passo 1: Escrever o teste de fumaça** — `test_smoke.py`:
  - `test_pyqgis_importa_e_versao_ltr`: `from qgis.core import Qgis`; `assert Qgis.version().startswith("3.44")`.
  - `test_plataforma_offscreen`: `assert os.environ["QT_QPA_PLATFORM"] == "offscreen"`.
- [ ] **Passo 2: Build** — `docker compose build worker`. Esperado: build conclui sem erro.
- [ ] **Passo 3: Rodar** — `docker compose run --rm worker pytest tests/test_smoke.py -v`. Esperado: 2 passed.
- [ ] **Passo 4: Checkpoint** (commit só se autorizado): `chore: scaffold do worker PyQGIS em Docker`.

---

### Task 2: Funções geométricas puras

**Arquivos:** Criar `worker/geolume_worker/geometry.py`, `worker/geolume_worker/errors.py`; Teste `worker/tests/test_geometry.py`

**Interfaces:**
- Produz:
  - `class InvalidInputError(Exception)` com atributos `codigo: str`, `mensagem: str`; `__init__(self, codigo: str, mensagem: str)`.
  - `utm_epsg_for(lon: float, lat: float) -> int` — regra D3; levanta `InvalidInputError("fora_da_cobertura", ...)`.
  - `grid_azimuth_deg(e1: float, n1: float, e2: float, n2: float) -> float` — `atan2(dE, dN)` normalizado em `[0, 360)`.
  - `format_dms(deg: float) -> str` — formato `"45°00'00\""`, segundos arredondados ao inteiro com vai-um correto.
  - `@dataclass(frozen=True) Vertex(id: str, e: float, n: float, azimute: str, distancia_m: float)`
  - `vertex_table(ring: list[tuple[float, float]]) -> list[Vertex]` — `ring` sem repetir o ponto de fechamento; o último vértice aponta para V1; ids `V1..Vn`; E/N e distância arredondados a 2 casas.

- [ ] **Passo 1: Escrever testes que falham:**
  - `test_epsg_brasilia` → `utm_epsg_for(-47.9, -15.8) == 31983`
  - `test_epsg_boa_vista_hemisferio_norte` → `utm_epsg_for(-60.7, 2.8) == 31975`
  - `test_epsg_fora_da_cobertura` → `utm_epsg_for(10.0, 50.0)` levanta `InvalidInputError` com `codigo == "fora_da_cobertura"`
  - `test_azimutes_cardeais` → (0,0)→(0,10)=0; →(10,0)=90; →(0,-10)=180; →(-10,0)=270; →(10,10)=45 (`pytest.approx`)
  - `test_format_dms` → `format_dms(45.0) == "45°00'00\""`; `format_dms(123.5125) == "123°30'45\""`; `format_dms(29.99999999) == "30°00'00\""`; `format_dms(359.9999999) == "0°00'00\""`
  - `test_vertex_table_quadrado` → ring `[(0,0),(0,10),(10,10),(10,0)]` gera 4 vértices; `V1.azimute == "0°00'00\""`, `V1.distancia_m == 10.0`; `V4` aponta para V1 com `azimute == "270°00'00\""`
- [ ] **Passo 2: Rodar** — `docker compose run --rm worker pytest tests/test_geometry.py -v`. Esperado: FAIL (módulo inexistente).
- [ ] **Passo 3: Implementar** as assinaturas acima (sem QGIS neste módulo).
- [ ] **Passo 4: Rodar de novo.** Esperado: todos PASS.
- [ ] **Passo 5: Checkpoint:** `feat(worker): funções geométricas puras`.

---

### Task 3: Métricas de tempo e memória

**Arquivos:** Criar `worker/geolume_worker/metrics.py`; Teste `worker/tests/test_metrics.py`

**Interfaces:**
- Produz:
  - `class PhaseTimer` com `phase(name: str)` (context manager) e propriedade `phases_ms -> dict[str, float]` (ms, 1 casa decimal, na ordem de inserção). Repetir o nome de uma fase soma os tempos.
  - `peak_rss_mb() -> float` — `ru_maxrss / 1024` (Linux informa em KB).
  - `current_rss_mb() -> float` — lê `VmRSS` de `/proc/self/status`.
  - `cgroup_memory_peak_mb() -> float | None` — lê `/sys/fs/cgroup/memory.peak`; `None` se o arquivo não existir.

- [ ] **Passo 1: Testes que falham:**
  - `test_phase_timer_registra_fase` → dentro de `phase("a")` com `time.sleep(0.05)`; `phases_ms["a"] >= 45`
  - `test_phase_timer_ordem_e_soma` → fases `a`, `b`, `a` → chaves `["a","b"]` e `a` acumula as duas
  - `test_phase_timer_registra_mesmo_com_excecao` → exceção dentro de `phase("x")` é propagada e `"x"` continua registrada
  - `test_rss_positivo` → `peak_rss_mb() > 0`, `current_rss_mb() > 0`, `peak_rss_mb() >= current_rss_mb() * 0.9`
  - `test_cgroup_peak_tipo` → resultado é `None` ou `float > 0`
- [ ] **Passo 2: Rodar** — `docker compose run --rm worker pytest tests/test_metrics.py -v` → FAIL.
- [ ] **Passo 3: Implementar.**
- [ ] **Passo 4: Rodar** → PASS.
- [ ] **Passo 5: Checkpoint:** `feat(worker): métricas de tempo e memória`.

---

### Task 4: Sessão QGIS reentrante

**Arquivos:** Criar `worker/geolume_worker/qgis_session.py`, `worker/tests/conftest.py`; Teste: casos em `worker/tests/test_smoke.py`

**Interfaces:**
- Produz:
  - `qgis_session() -> ContextManager[QgsApplication]` — na primeira entrada: `QgsApplication.setPrefixPath("/usr", True)`, `QgsApplication([], False)`, `initQgis()`. Entradas aninhadas reusam a mesma instância (contador de referências). `exitQgis()` só quando o contador volta a 0.
  - `is_qgis_initialized() -> bool`.
  - Fixture pytest `qgis_app` com escopo `session` em `conftest.py`, usando `qgis_session()`. Fixture `fixtures_dir -> Path` apontando para `tests/fixtures`.

- [ ] **Passo 1: Testes que falham** (em `test_smoke.py`):
  - `test_sessao_aninhada_reusa_instancia` → `with qgis_session() as a: with qgis_session() as b: assert a is b`
  - `test_sessao_permite_criar_camada(qgis_app)` → `QgsVectorLayer("Polygon?crs=EPSG:4674", "t", "memory").isValid()`
- [ ] **Passo 2: Rodar** → FAIL.
- [ ] **Passo 3: Implementar.**
- [ ] **Passo 4: Rodar** `pytest tests/test_smoke.py -v` → PASS, e o processo termina com código 0 (sem segfault no `exitQgis`).
- [ ] **Passo 5: Checkpoint:** `feat(worker): sessão QGIS headless reentrante`.

---

### Task 5: Carregamento e validação da entrada

**Arquivos:** Criar `worker/geolume_worker/input_loader.py` e todas as fixtures em `worker/tests/fixtures/`; Teste `worker/tests/test_input_loader.py`

**Interfaces:**
- Consome: `InvalidInputError` (Task 2), fixture `qgis_app` (Task 4).
- Produz: `@dataclass LoadedInput(layer: QgsVectorLayer, geometry: QgsGeometry, source_crs: str)`; `load_input(path: Path) -> LoadedInput`.
- Códigos de erro, na ordem de verificação: `arquivo_nao_encontrado`, `arquivo_muito_grande` (> 10 MB), `json_invalido`, `sem_feicoes`, `multiplas_feicoes`, `geometria_nao_poligonal`, `multiplas_partes`, `poligono_com_furos`, `geometria_invalida` (`not geometry.isGeosValid()`), `excesso_de_vertices` (> 5 000), `crs_nao_suportado` (authid fora de `{"EPSG:4326","EPSG:4674"}`).

- [ ] **Passo 1: Testes que falham:**
  - `test_carrega_lote_simples` → `source_crs in {"EPSG:4326","EPSG:4674"}`, geometria é polígono de 1 parte com 4 vértices distintos
  - `test_erros_de_entrada` parametrizado, (fixture → código): `inexistente.geojson → arquivo_nao_encontrado`, `invalido.json → json_invalido`, `vazio.geojson → sem_feicoes`, `multiplas_feicoes.geojson → multiplas_feicoes`, `linha.geojson → geometria_nao_poligonal`, `com_furo.geojson → poligono_com_furos`, `autointersecao.geojson → geometria_invalida`
  - `test_arquivo_muito_grande(tmp_path)` → arquivo de 10 MB + 1 byte → `arquivo_muito_grande`
  - `test_excesso_de_vertices(tmp_path)` → polígono gerado com 5 001 vértices → `excesso_de_vertices`
- [ ] **Passo 2: Rodar** `pytest tests/test_input_loader.py -v` → FAIL.
- [ ] **Passo 3: Implementar** — `json.loads` antes do OGR para separar `json_invalido`; depois `QgsVectorLayer(str(path), "entrada", "ogr")`.
- [ ] **Passo 4: Rodar** → PASS.
- [ ] **Passo 5: Checkpoint:** `feat(worker): validação da entrada GeoJSON`.

---

### Task 6: Processamento e PDF

**Arquivos:** Criar `worker/geolume_worker/processing.py`, `worker/geolume_worker/layout.py`; Testes `worker/tests/test_processing.py`, `worker/tests/test_layout.py`

**Interfaces:**
- Consome: `LoadedInput`, `utm_epsg_for`, `vertex_table`, `Vertex`.
- Produz:
  - `@dataclass ParcelSummary(epsg: int, area_ha: float, perimetro_m: float, vertices: list[Vertex], geometry_utm: QgsGeometry)` — área com 4 casas, perímetro com 2.
  - `process(loaded: LoadedInput) -> ParcelSummary` — fuso pelo centroide na origem; `QgsCoordinateTransform` com `QgsProject.instance().transformContext()`; os vértices partem do primeiro ponto do anel externo, no sentido do arquivo.
  - `export_map_pdf(summary: ParcelSummary, output_path: Path, title: str = "GeoLume — Mapa de Localização") -> Path` — camada em memória com `geometry_utm`; `QgsPrintLayout` A4 paisagem; mapa com extensão = bbox × 1,2; `QgsLayoutItemScaleBar`, seta de norte (`QgsLayoutItemPicture` com o SVG padrão de norte), rótulo com título, `EPSG:{epsg}` e `Área: {area_ha} ha`; `QgsLayoutExporter.exportToPdf` a 300 dpi. Usa um `QgsProject` próprio por chamada (não o `instance()`) para evitar estado vazando entre jobs. Levanta `RuntimeError` se o exportador não devolver `Success`.

- [ ] **Passo 1: Testes que falham:**
  - `test_process_lote_simples` → `epsg == 31983`; `area_ha == approx(1.18, abs=0.02)`; `perimetro_m == approx(435, abs=3)`; `len(vertices) == 4`; `vertices[0].id == "V1"`
  - `test_process_hemisferio_norte` → `lote_boa_vista.geojson` → `epsg == 31975`
  - `test_process_europa_rejeitado` → `europa.geojson` → `InvalidInputError("fora_da_cobertura")`
  - `test_pdf_gerado(tmp_path)` → arquivo existe, começa com `b"%PDF"`, tem mais de 1 KB
  - `test_pdf_contem_texto(tmp_path)` → `subprocess.run(["pdftotext", pdf, "-"])` contém `"GeoLume"` e `"31983"` (garante que as fontes renderizaram)
  - `test_pdf_uma_pagina(tmp_path)` → `pdfinfo` informa `Pages: 1`
- [ ] **Passo 2: Rodar** `pytest tests/test_processing.py tests/test_layout.py -v` → FAIL.
- [ ] **Passo 3: Implementar `process`.** Rodar `test_processing.py` → PASS.
- [ ] **Passo 4: Implementar `export_map_pdf`.** Rodar `test_layout.py` → PASS.
- [ ] **Passo 5: Checkpoint:** `feat(worker): reprojeção, métricas do lote e PDF`.

---

### Task 7: `run_job` — contrato para CLI e futuro Celery

**Arquivos:** Criar `worker/geolume_worker/job.py`; Teste `worker/tests/test_job.py`

**Interfaces:**
- Consome: todos os módulos anteriores.
- Produz:
  - `@dataclass JobResult(job_id: str, pdf_path: Path, json_path: Path, phases_ms: dict[str, float], peak_rss_mb: float, rss_before_mb: float, rss_after_mb: float)`.
  - `run_job(input_path: Path, output_dir: Path, job_id: str | None = None) -> JobResult` — exige uma sessão QGIS já ativa (se `not is_qgis_initialized()`, levanta `RuntimeError("sessao QGIS nao inicializada")`). Não inicializa o QGIS: essa é a costura para o Celery. `job_id` padrão: `uuid4().hex`. Grava em `output_dir / job_id /` os arquivos `mapa.pdf` e `resultado.json`. Fases: `carregar`, `processar`, `renderizar_pdf`, `gravar_json`, `total_job`.
  - Esquema de `resultado.json` (UTF-8, `ensure_ascii=False`):
    `{"versao_esquema": 1, "job_id", "entrada": <nome do arquivo>, "crs_saida": "EPSG:<n>", "area_ha", "perimetro_m", "vertices": [{"id","e","n","azimute","distancia_m"}], "metricas": {"fases_ms", "pico_rss_mb", "rss_antes_mb", "rss_depois_mb"}}`
  - Erro de entrada: `InvalidInputError` é propagado; nenhuma pasta de job fica para trás.

- [ ] **Passo 1: Testes que falham:**
  - `test_run_job_gera_artefatos(qgis_app, tmp_path)` → PDF e JSON existem em `tmp_path/<job_id>/`; JSON tem `versao_esquema == 1`, 4 vértices e `crs_saida == "EPSG:31983"`; `phases_ms` tem as 5 chaves
  - `test_run_job_job_id_explicito` → `job_id="abc"` gera a pasta `tmp_path/"abc"`
  - `test_run_job_sem_sessao` → roda o código num subprocesso (`python -c`) sem sessão e verifica que sai com `RuntimeError`
  - `test_run_job_entrada_invalida_nao_deixa_pasta` → `linha.geojson` levanta erro e `tmp_path` continua vazio
  - `test_dois_jobs_na_mesma_sessao` → 2 chamadas seguidas bem-sucedidas; JSONs com `job_id` distintos (valida o caminho "quente" do Celery)
- [ ] **Passo 2: Rodar** `pytest tests/test_job.py -v` → FAIL.
- [ ] **Passo 3: Implementar.**
- [ ] **Passo 4: Rodar a suíte inteira** `docker compose run --rm worker pytest -v` → tudo PASS.
- [ ] **Passo 5: Checkpoint:** `feat(worker): run_job com artefatos e métricas`.

---

### Task 8: CLI

**Arquivos:** Criar `worker/geolume_worker/__main__.py`; Teste `worker/tests/test_cli.py`

**Interfaces:**
- Consome: `qgis_session`, `run_job`, `PhaseTimer`.
- Produz: `python -m geolume_worker run --input <path> --output <dir> [--job-id <id>]`.
  - Sucesso: código 0; stdout recebe 1 linha JSON `{"status": "ok", "job_id", "pdf", "json", "fases_ms"}`, com `fases_ms` incluindo `inicializacao_qgis`.
  - Entrada inválida: código 2; stderr recebe 1 linha JSON `{"status": "erro", "codigo", "mensagem"}`.
  - Erro inesperado: código 1; traceback no stderr.

- [ ] **Passo 1: Testes que falham** (via `subprocess.run([sys.executable, "-m", "geolume_worker", ...])`):
  - `test_cli_sucesso` → código 0; stdout é JSON com `status == "ok"`; `"inicializacao_qgis" in fases_ms`; o PDF existe
  - `test_cli_entrada_invalida` → `linha.geojson` → código 2; stderr JSON com `codigo == "geometria_nao_poligonal"`
  - `test_cli_sem_argumentos` → código diferente de 0
- [ ] **Passo 2: Rodar** `pytest tests/test_cli.py -v` → FAIL.
- [ ] **Passo 3: Implementar** com `argparse`.
- [ ] **Passo 4: Rodar** → PASS. Verificação manual: `docker compose run --rm worker python -m geolume_worker run --input tests/fixtures/lote_simples.geojson --output /saida`. Esperado: `.\saida\<job_id>\mapa.pdf` aparece no Windows e abre corretamente.
- [ ] **Passo 5: Checkpoint:** `feat(worker): CLI`.

---

### Task 9: Benchmark e documentação de medição

**Arquivos:** Criar `worker/scripts/benchmark.py`, `worker/README.md`; Teste `worker/tests/test_benchmark.py`

**Interfaces:**
- Consome: `qgis_session`, `run_job`, métricas.
- Produz: `python scripts/benchmark.py --input <path> --runs <N> --output <dir>`. Mede `inicializacao_qgis` uma vez e depois N jobs quentes. Grava `<output>/benchmark-<AAAAMMDD-HHMMSS>.json`:
  `{"versao_qgis", "cpus": os.cpu_count(), "mem_total_mb" (de /proc/meminfo), "runs", "inicializacao_qgis_ms", "fases_ms": {<fase>: {"min","mediana","p95","max"}}, "rss_por_job_mb": [rss_depois de cada job], "pico_rss_mb", "cgroup_pico_mb", "meta_60s_atingida": total_job p95 + inicializacao_qgis_ms < 60000}`. Também imprime uma tabela legível no stdout.
- Função testável: `summarize(values: list[float]) -> dict[str, float]` (p95 pelo método nearest-rank).

- [ ] **Passo 1: Testes que falham:**
  - `test_summarize` → `summarize([1,2,3,4,5,6,7,8,9,10]) == {"min":1,"mediana":5.5,"p95":10,"max":10}`
  - `test_benchmark_executa(tmp_path)` → subprocesso com `--runs 2` → código 0; o JSON existe, `runs == 2`, `len(rss_por_job_mb) == 2`
- [ ] **Passo 2: Rodar** → FAIL.
- [ ] **Passo 3: Implementar** `benchmark.py` e o `README.md` com os comandos PowerShell:
  - `docker compose build worker`
  - `docker compose run --rm worker pytest -v`
  - `docker compose run --rm worker python scripts/benchmark.py --input tests/fixtures/lote_simples.geojson --runs 20 --output /saida`
  - partida a frio do container: `Measure-Command { docker compose run --rm worker python -m geolume_worker run --input tests/fixtures/lote_simples.geojson --output /saida }`
  - anotar CPU/RAM alocadas ao Docker Desktop (Settings → Resources) junto com cada resultado
- [ ] **Passo 4: Rodar** `pytest tests/test_benchmark.py -v` → PASS, depois a suíte completa → PASS.
- [ ] **Passo 5: Rodar o benchmark real (20 execuções)** e entregar ao usuário: tempo a frio, p95 quente, pico de RSS, pico do cgroup e se `meta_60s_atingida`. Os resultados **não** vão para o AI Brain sem aprovação.
- [ ] **Passo 6: Checkpoint:** `feat(worker): benchmark e instruções de medição`.

---

## Review Focus

1. **Polígono com vértice de fechamento repetido ou vértices duplicados consecutivos** → a tabela não pode ter vértice com distância 0. Teste a acrescentar na Task 6 (`process`): `test_vertices_duplicados_removidos` (fixture com um ponto repetido → 4 vértices, nenhuma `distancia_m == 0`).
2. **GeoJSON com membro `crs` legado apontando para UTM ou outro CRS** → deve ser rejeitado com `crs_nao_suportado`, não reprojetado errado. Teste na Task 5: `test_crs_legado_utm_rejeitado` (fixture `crs_utm.geojson` com `"crs": {"type":"name","properties":{"name":"EPSG:31983"}}`).
3. **Vazamento de memória entre jobs quentes** → o RSS não deve crescer sem limite. Teste na Task 9: `test_rss_estavel` (5 jobs; `rss_por_job_mb[-1] - rss_por_job_mb[1] < 50`).
4. **Pasta de saída inexistente ou sem permissão de escrita** → erro claro, não traceback do QGIS. Teste na Task 7: `test_run_job_cria_output_dir` (`output_dir` inexistente é criado).
5. **Polígono muito pequeno (< 1 m²) ou enorme (> 100 000 ha)** → o PDF continua legível (a barra de escala não quebra). Teste na Task 6: `test_pdf_poligono_minusculo` com um quadrado de 0,00001° → PDF gerado e `pdftotext` contém `"GeoLume"`.

## Fora do escopo (próximas etapas)

FastAPI, Celery/Redis, PostGIS, storage de objetos, multi-tenancy, autenticação, memorial em DOCX/PDF, mapa de fundo, layout oficial do cliente.

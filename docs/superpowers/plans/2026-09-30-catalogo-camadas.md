# Catálogo de camadas e estilos cartográficos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catálogo único de camadas servido pelo servidor, estilos de polígono (Padrão, Técnico, Preto e branco) e alfa do preenchimento idênticos na pré-visualização e no `mapa.pdf`.

**Architecture:** `geolume_worker/camadas.py` (sem QGIS) é a única fonte de camadas, estilos e regra do alfa. `prancha.py` passa a validar `estilo` e `alfa_preenchimento`; `layout.py` monta o símbolo QGIS a partir deles; `api.py` serve `GET /camadas`. O navegador busca o catálogo, monta o painel e aplica o mesmo estilo no Leaflet; `prancha.js` espelha a validação.

**Tech Stack:** Python 3 + FastAPI + PyQGIS 3.44 (container `geolume-worker:poc`), Celery/Redis, PostgreSQL/PostGIS (JSONB `jobs.prancha`), JavaScript puro + Leaflet, `node --test`, Playwright (projetos `chrome-desktop` 1440×900 e `chrome-mobile` 390×844), Chrome DevTools MCP, Codex (revisão somente leitura).

**Spec:** `docs/superpowers/specs/2026-09-30-catalogo-camadas-design.md`

## Global Constraints

- Catálogo definido só no servidor (`camadas.py`); o navegador não tem lista fixa de camadas.
- Satélite, limites municipais, hidrografia e rodovias: `situacao="depende_fonte_oficial"`, `url=None`, não ativáveis. Topografia e edificações: `"planejada"`.
- OSM só na pré-visualização, com atribuição "© Contribuidores do OpenStreetMap" e aviso "Somente na pré-visualização — não entra no PDF".
- Geração do PDF sem nenhuma chamada de rede; nenhuma camada raster no projeto QGIS.
- `alfa_preenchimento` ∈ [0, 1], padrão `0.35`; inválido é **recusado** (nunca corrigido) no navegador, na API e no worker, com código `alfa_invalido` e mensagem "Opacidade do preenchimento deve estar entre 0 e 1."
- Alfa único: `alfa8 = round(alfa * 255)`; QGIS usa `alfa8`, Leaflet usa `alfa8 / 255`. Contorno sempre opacidade 1.
- Estilos: `padrao` (#C80000 / #FFC800, 0,6 mm / 3 px), `tecnico` (#1F2937 / #9CA3AF, 0,35 mm / 2 px), `pb` (#000000 / #FFFFFF, 0,5 mm / 2 px). Id desconhecido ⇒ `estilo_invalido`, "Estilo do polígono inválido."
- Jobs antigos (`prancha` nulo ou sem as chaves novas) ⇒ `estilo="padrao"`, `alfa_preenchimento=0.35`. Sem migração de banco.
- Não alterar Dockerfile, docker-compose.yml, schema do banco nem o volume `geolume-postgis`. Manter QGIS, Celery, Redis e PostGIS.
- Autenticação, autorização (tenant/dono), CSRF, histórico, downloads, logo e memorial sem mudança de comportamento.
- O projeto não é repositório git: no lugar de commit, cada tarefa termina com a suíte da camada tocada verde.

Comandos (a partir de `D:\Projetos\Geolume`):

- Worker/API: `docker compose run --rm worker python3 -m pytest -q <arquivo>`
- JS unit: `cd tests-web && npm run test:unit`
- E2E: `docker compose up -d api redis postgis celery` e `cd tests-web && npx playwright test <spec>`

## Review Focus

1. Alfa enviado como texto com vírgula (`"0,5"`) ou vazio vindo do formulário: `""` ⇒ padrão; `"0,5"` ⇒ recusado com `alfa_invalido` (a API não adivinha locale). Teste em Task 2 (`test_alfa_texto_com_virgula_e_recusado`) e Task 3.
2. Job antigo com `prancha` gravado contendo só as cores antigas: página do histórico e PDF reprocessado não podem quebrar. Teste em Task 2 (`test_prancha_antiga_sem_chaves_novas`) e Task 3 (`test_job_antigo_devolve_estilo_padrao`).
3. Catálogo 401 depois de a sessão expirar: o `onUnauthorized` existente deve disparar, não o aviso "catálogo indisponível". Teste em Task 5 (`getCamadas 401 chama onUnauthorized`).
4. Usuário escolhe estilo Técnico e depois muda uma cor: o job grava `estilo="tecnico"` + a cor alterada; PDF usa espessura 0,35 mm com a cor nova. Teste em Task 4 (`test_estilo_tecnico_com_cor_personalizada`) e Task 7.
5. Alfa 0 (preenchimento invisível) e alfa 1: PDF gerado sem erro e contorno continua visível na tela. Teste em Task 4 e Task 6.

---

### Task 1: `camadas.py` — catálogo, estilos e regra do alfa

**Files:**
- Create: `worker/geolume_worker/camadas.py`
- Test: `worker/tests/test_camadas.py`

**Interfaces:**
- Produces:
  - `Camada` (dataclass frozen) com os campos do contrato da spec; `Fonte` e `Atribuicao` (dataclasses frozen).
  - `CAMADAS: tuple[Camada, ...]` — as 9 camadas da tabela "Catálogo inicial" da spec, nessa ordem.
  - `Estilo` (frozen: `id, nome, contorno, preenchimento, espessura_mm: float, espessura_px: int`); `ESTILOS: tuple[Estilo, ...]` = padrao, tecnico, pb.
  - `ESTILO_PADRAO = "padrao"`, `ALFA_PADRAO = 0.35`.
  - `estilo_por_id(id: str) -> Estilo` (levanta `KeyError`).
  - `validar_alfa(valor) -> float` — `None`/`""` ⇒ `ALFA_PADRAO`; aceita `int`/`float` (não `bool`) e `str` com ponto decimal; recusa `NaN`, infinito, fora de [0, 1] com `InvalidInputError("alfa_invalido", ...)`.
  - `alfa8(alfa: float) -> int` = `round(alfa * 255)`.
  - `catalogo_publico() -> dict` = `{"camadas": [...], "estilos": [...], "alfa_padrao": 0.35}`, só tipos JSON.

- [ ] **Step 1: RED — testes do contrato**
  `test_ids_unicos_e_validos` (regex `[a-z0-9_]{1,32}`); `test_nao_disponivel_sem_url_sem_pdf_invisivel`; `test_url_so_em_raster_disponivel_com_atribuicao_https`; `test_exporta_pdf_so_vetor_sem_url` (e o único é `poligono`); `test_poligono_acima_de_todo_mapa_base` (ordem 100 > ordem de todo `grupo=="base"`); `test_satelite_e_fontes_oficiais_desabilitados` (ids `satelite, limites_municipais, hidrografia, rodovias` ⇒ `depende_fonte_oficial`); `test_ruas_osm_aviso_e_atribuicao` (textos exatos das Global Constraints); `test_atribuicao_sem_html` (nenhum `<`, `>`, `&` em textos do catálogo); `test_catalogo_publico_serializa_em_json` (`json.dumps` sem erro; chaves exatamente as do contrato).
- [ ] **Step 2: RED — testes de estilo e alfa**
  `test_estilos_valores_da_spec` (tabela exata); `test_estilo_desconhecido_keyerror`; parametrizado `test_alfa_aceito` com `0, 0.0, 0.35, 1, "0.5"`; `test_alfa_ausente_e_padrao` com `None, ""`; parametrizado `test_alfa_recusado` com `-0.01, 1.01, "abc", "0,5", float("nan"), float("inf"), True, [], {}` ⇒ `InvalidInputError` com `codigo == "alfa_invalido"` e mensagem fixa (sem eco do valor); `test_alfa8` (`0.35 → 89`, `0 → 0`, `1 → 255`).
- [ ] **Step 3: Rodar** `docker compose run --rm worker python3 -m pytest -q tests/test_camadas.py` — esperado: FAIL (`ModuleNotFoundError: geolume_worker.camadas`).
- [ ] **Step 4: Implementar `camadas.py`** conforme Interfaces; sem importar QGIS nem fazer I/O.
- [ ] **Step 5: Rodar de novo** — esperado: PASS.

### Task 2: `prancha.py` — `estilo` e `alfa_preenchimento`

**Files:**
- Modify: `worker/geolume_worker/prancha.py` (`Prancha`, `validar_prancha`)
- Test: `worker/tests/test_prancha.py`

**Interfaces:**
- Consumes: `ESTILOS`, `ESTILO_PADRAO`, `ALFA_PADRAO`, `validar_alfa` (Task 1).
- Produces: `Prancha.estilo: str = "padrao"`, `Prancha.alfa_preenchimento: float = 0.35`; `validar_prancha` aceita as chaves `estilo` e `alfa_preenchimento`; `Prancha().padrao` continua verdadeiro sem opções.

- [ ] **Step 1: RED** — `test_sem_opcoes_e_o_padrao_atual` estendido (estilo `padrao`, alfa `0.35`); `test_prancha_antiga_sem_chaves_novas` (dict só com cores antigas ⇒ padrões novos); `test_estilo_valido` (cada id); `test_estilo_invalido` (`"urbano"`, `1`, `None` vira padrão) com código `estilo_invalido`; `test_alfa_invalido_recusado` (delegando a casos da Task 1); `test_alfa_texto_com_virgula_e_recusado`; `test_como_dict_inclui_estilo_e_alfa`.
- [ ] **Step 2: Rodar** `... pytest -q tests/test_prancha.py` — FAIL nos testes novos.
- [ ] **Step 3: Implementar** os dois campos em `Prancha` e a validação em `validar_prancha` (vazio/ausente ⇒ padrão; mesma mensagem fixa sem ecoar o valor, como `legenda`).
- [ ] **Step 4: Rodar** `tests/test_prancha.py tests/test_job.py` — PASS (worker revalida via `job.py:44`, sem mudança lá).

### Task 3: API — `GET /camadas` e novos campos do job

**Files:**
- Modify: `worker/api.py` (nova rota; `enqueue_job` recebe `estilo` e `alfa_preenchimento` como `Form()`)
- Test: `worker/tests/test_api_prancha.py`, `worker/tests/test_api_authz.py`

**Interfaces:**
- Consumes: `catalogo_publico()` (Task 1), `validar_prancha` (Task 2).
- Produces: `GET /camadas` → 200 `catalogo_publico()` com header `Cache-Control: private, max-age=300`; exige `current_user` (401 sem sessão). O job devolvido por `/jobs/{task_id}` passa a ter `prancha.estilo` e `prancha.alfa_preenchimento` quando a prancha não é nula.

- [ ] **Step 1: RED** — `test_camadas_exige_sessao` (401); `test_camadas_devolve_catalogo` (igual a `catalogo_publico()`; header de cache); `test_camadas_sem_url_nas_indisponiveis`; `test_post_alfa_invalido_422` (casos `"-0.1"`, `"2"`, `"abc"`, `"0,5"`, `"NaN"`; detalhe = mensagem fixa; nada enfileirado, nenhum arquivo em `inputs/`); `test_post_estilo_invalido_422`; `test_post_estilo_e_alfa_gravados_e_devolvidos`; `test_post_sem_campos_novos_prancha_nula` (envio sem personalização continua `prancha=None`); `test_job_antigo_devolve_estilo_padrao` (registro com prancha antiga ⇒ `estilo="padrao"`, `alfa_preenchimento=0.35`); em `test_api_authz.py`, `test_camadas_mesma_resposta_para_qualquer_usuario` (sem dados de tenant).
- [ ] **Step 2: Rodar** `... pytest -q tests/test_api_prancha.py tests/test_api_authz.py` — FAIL.
- [ ] **Step 3: Implementar** a rota e os dois `Form()` repassados a `validar_prancha`.
- [ ] **Step 4: Rodar** o arquivo e depois a suíte completa `docker compose run --rm worker python3 -m pytest -q` — PASS (442+ testes).

### Task 4: PDF — símbolo do estilo e alfa, sem rede

**Files:**
- Modify: `worker/geolume_worker/layout.py` (`_simbolo`; remover `_ALFA_PREENCHIMENTO`)
- Test: `worker/tests/test_layout_prancha.py`

**Interfaces:**
- Consumes: `estilo_por_id`, `alfa8` (Task 1); `Prancha.estilo`, `Prancha.alfa_preenchimento` (Task 2).
- Produces: `_simbolo(prancha)` com cor de preenchimento `(r,g,b,alfa8(prancha.alfa_preenchimento))`, contorno `prancha.cor_contorno` opaco e `outline_width = estilo.espessura_mm`. Amostra da legenda usa o mesmo símbolo (já usa).

- [ ] **Step 1: RED** — parametrizado por estilo `test_simbolo_do_estilo` (lê `symbolLayer(0)`: `fillColor().alpha() == alfa8(...)`, `strokeColor().alpha() == 255`, `strokeWidth() == espessura_mm`); `test_alfa_padrao_89`; `test_alfa_zero_e_um_geram_pdf` (PDF válido, `pdfinfo` 1 página); `test_estilo_tecnico_com_cor_personalizada`; `test_legenda_amostra_igual_ao_simbolo` (layout lateral); `test_projeto_so_camadas_vetoriais_de_memoria` (`all(l.providerType() == "memory")` e nenhum `QgsRasterLayer`); `test_pdf_sem_rede` (monkeypatch em `QgsNetworkAccessManager.instance().get`/`socket.socket.connect` levantando erro; `export_map_pdf` conclui).
- [ ] **Step 2: Rodar** `... pytest -q tests/test_layout_prancha.py` — FAIL em alfa/espessura.
- [ ] **Step 3: Implementar** `_simbolo` usando o estilo; tirar a constante antiga.
- [ ] **Step 4: Rodar** `tests/test_layout_prancha.py tests/test_layout.py tests/test_layout_limites.py` — PASS; conferir as 15 combinações de layout × vértices do slice anterior continuam PASS.

### Task 5: JS puro — catálogo, estilos e validação do alfa

**Files:**
- Modify: `worker/web/js/layers.js`, `worker/web/js/prancha.js`, `worker/web/js/api.js`
- Test: `tests-web/unit/layers.test.mjs`, `tests-web/unit/prancha.test.mjs`, `tests-web/unit/api.test.mjs`

**Interfaces:**
- Consumes: formato de `catalogo_publico()` (Task 1/3).
- Produces:
  - `api.getCamadas()` → `request("/camadas")`.
  - `layers.js`: remove `BASEMAPS`; `DEFAULT_VIEW = { base: "ruas_osm", polygon: true }` (sem opacity); `parseCatalogo(body) -> { camadas, estilos, alfaPadrao } | null` (descarta camada com `url` não-`https:` ou com `url` e `situacao != "disponivel"`); `grupos(catalogo) -> { disponiveis, dependemFonte, planejadas }` ordenados por `ordem`; `basemaps(catalogo)`; `camadaAtivavel(camada) -> bool`; `withBase(view, id, catalogo)`; `atribuicaoHtml(atribuicao) -> string` (escapa `& < > " '`, link `rel="noopener noreferrer" target="_blank"` só se `https:`); `CATALOGO_VAZIO` (só `nenhum` + `poligono`, usado quando o catálogo falha); `legendItems(view, catalogo, { tilesFailed })` — inclui o aviso da camada base quando houver.
  - `prancha.js`: `PRANCHA_PADRAO` ganha `estilo: "padrao"`, `alfa_preenchimento: 0.35`; estilos vêm só do catálogo; `validateAlfa(valor) -> { ok, value } | { ok: false, message }` com as mesmas regras de `validar_alfa`; `alfaFromPercent(percent)` (0–100 inteiro ⇒ `percent/100`, fora da faixa ⇒ `{ ok: false }`); `alfa8(alfa)`; `validatePrancha` valida `estilo` contra a lista recebida e `alfa_preenchimento`; `pranchaFields` envia `alfa_preenchimento` como `String(valor)` com ponto; `polygonStyle(prancha, estilos) -> { color, fillColor, fillOpacity: alfa8/255, weight: espessura_px, opacity: 1 }`.

- [ ] **Step 1: RED** — mesma tabela de casos do alfa da Task 1 (aceitos/recusados, mensagem idêntica); `alfa8(0.35) === 89`; `polygonStyle` para os três estilos; `validatePrancha` recusa estilo fora do catálogo; `pranchaFields` sem personalização continua `[]`; `parseCatalogo` recusa `url` `http:`/`javascript:` e URL em camada indisponível; `grupos` separa as 9 camadas em 3/4/2; `camadaAtivavel` falso para satélite; `atribuicaoHtml` com `<img onerror>` sai escapado; `legendItems` inclui "Somente na pré-visualização — não entra no PDF" com `ruas_osm`; `getCamadas 401 chama onUnauthorized`; `getCamadas` falha de rede ⇒ `ApiError` status 0.
- [ ] **Step 2: Rodar** `cd tests-web && npm run test:unit` — FAIL nos novos.
- [ ] **Step 3: Implementar** as funções puras (sem DOM, sem Leaflet).
- [ ] **Step 4: Rodar** — PASS (114+ testes).

### Task 6: Mapa — ordem por panes e estilo único

**Files:**
- Modify: `worker/web/js/map.js`
- Test: `tests-web/unit/map.test.mjs`

**Interfaces:**
- Consumes: `withBase`, `CATALOGO_VAZIO`, `polygonStyle`, `alfa8` (Task 5).
- Produces: `createMapView(container, { leaflet, onChange, catalogo })`; `setCatalogo(catalogo)`; `setStyle({ estilo, cor_contorno, cor_preenchimento, alfa_preenchimento })` substitui `setColors` e `setOpacity`; panes `geolume-base` (zIndex `200 + ordem`) e `geolume-poligono` (zIndex `400 + ordem`) criados em `show`.

- [ ] **Step 1: RED** (Leaflet falso já usado em `map.test.mjs`) — `pane do polígono acima do mapa-base`; `setBase recusa camada não ativável e não cria tileLayer`; `sem catálogo nenhum tileLayer é criado`; `setStyle aplica polygonStyle de uma vez`; `alfa 0 mantém contorno com opacity 1`; `tileerror marca tilesFailed e mantém o polígono` (existente, adaptado); `reset volta ao estilo padrão`.
- [ ] **Step 2: Rodar** — FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar** `npm run test:unit` — PASS.

### Task 7: Interface — painel "Camadas do mapa", cartões de estilo e alfa na prancha

**Files:**
- Modify: `worker/web/js/render.js`, `worker/web/js/app.js`, `worker/web/index.html`, `worker/web/styles.css`
- Test: `tests-web/e2e/camadas.spec.mjs`, `tests-web/e2e/prancha.spec.mjs`

**Interfaces:**
- Consumes: Tasks 5 e 6.
- Produces (data-testid): `camadas-painel`, `camadas-grupo-disponiveis`, `camadas-grupo-fonte-oficial`, `camadas-grupo-planejadas`, `camada-item` com `data-camada-id` e `aria-disabled`, `camadas-erro`, `prancha-estilos` com `estilo-card` (`data-estilo`), `prancha-input-alfa` (range 0–100, rótulo "Opacidade do preenchimento (entra no PDF)"), `prancha-alfa-valor`. O controle `polygon-opacity` do painel do mapa é removido: a opacidade existe só na prancha. Ao abrir job do histórico, `mapView.setStyle(job.prancha ?? padrão)`.

- [ ] **Step 1: RED — E2E** (desktop e mobile, reaproveitando `stubTiles`/`externas`):
  - `painel mostra três grupos e satélite desabilitado com aviso`;
  - `ruas OSM com aviso "Somente na pré-visualização — não entra no PDF" e atribuição`;
  - `catálogo com 500 ⇒ camadas-erro visível, polígono desenhado, zero requisições a tile.openstreetmap.org`;
  - `tiles bloqueados ⇒ legenda indisponível e polígono visível` (existente, adaptado);
  - `estilo Técnico + alfa 60% ⇒ path.geolume-poligono com stroke #1F2937, stroke-width 2, fill-opacity 0.6; job devolve estilo e alfa`;
  - `alfa inválido forçado no DOM (value="150") bloqueia envio com a mensagem fixa`;
  - `PDF do job com estilo Técnico: pdftotext contém "Limite do imóvel" (legenda lateral) e job concluído`;
  - `job antigo sem prancha abre com estilo padrão e fill-opacity 89/255`;
  - regressão: login, logout, histórico e três downloads continuam passando (`auth`, `operacao`, `hardening` sem mudança).
- [ ] **Step 2: Rodar** `npx playwright test e2e/camadas.spec.mjs e2e/prancha.spec.mjs` — FAIL.
- [ ] **Step 3: Implementar** `renderCamadasPanel(root, catalogo | null, view)` e `renderEstiloCards(root, estilos, selecionado)` em `render.js` (tudo por `textContent`, atribuição via `atribuicaoHtml`); `app.js` chama `api.getCamadas()` depois do login e usa `CATALOGO_VAZIO` em falha (exceto 401); escolher estilo preenche as cores; HTML/CSS com foco visível e layout responsivo em 390 px.
- [ ] **Step 4: Rodar** toda a E2E `npx playwright test` — PASS nos dois projetos (180+ testes).

### Task 8: Verificação final — PDF, DevTools e Codex

**Files:** nenhum de produção; correções só com teste de regressão na tarefa correspondente.

- [ ] **Step 1: Suítes completas** — pytest completo, `npm run test:unit`, `npx playwright test`. Registrar números reais.
- [ ] **Step 2: PDFs reais** — gerar um job por estilo (+ alfa 0 e 1) com `gleba_rural_exemplo.geojson`; `pdfinfo` (1 página, A4 paisagem) e `pdftotext` (legenda); inspecionar cor/alfa do preenchimento no conteúdo do PDF (operador `gs`/`ca` = 89/255 no padrão).
- [ ] **Step 3: Rede no worker** — gerar o PDF num container do worker sem rede (`docker compose run --rm --network none worker python3 -m geolume_worker run ...`) e confirmar sucesso. Não altera compose nem volume.
- [ ] **Step 4: Chrome DevTools** (desktop 1440×900 e mobile 390×844): aba Rede (nenhum tile com `/camadas` bloqueado; `/camadas` com 401 sem sessão), console sem erros, árvore de acessibilidade do painel (itens desabilitados anunciados, rótulos, ordem de foco), captura de tela dos três grupos.
- [ ] **Step 5: Codex somente leitura** — pedir revisão de `camadas.py`, `prancha.py`, `layout.py`, `api.py` (rota e campos) e JS alterado, com foco em: validação do alfa nas três camadas, escape da atribuição, catálogo sem URL em camadas indisponíveis, compatibilidade de jobs antigos, PDF sem rede, autorização de `/camadas`.
- [ ] **Step 6: Achados** — para cada achado confirmado: teste RED, correção, suítes verdes. Achado não reproduzido é reportado, não corrigido.
- [ ] **Step 7: Atualizar** `worker/README.md` (rota `/camadas`, campos novos da prancha) e relatar resultados reais ao usuário.

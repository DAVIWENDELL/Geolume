# GeoLume — Interface de operação — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evoluir `worker/web/` para uma tela Monitor/Operate que envia GeoJSON, acompanha o job por `GET /jobs/{task_id}`, lista o histórico e entrega os três arquivos.

**Architecture:** HTML/CSS/JS puro com ES modules nativos, servido pelo `StaticFiles` já montado em `api.py`. Lógica pura (`model.js`, `api.js`, `poller.js`) testada com `node:test`; DOM (`render.js`, `app.js`) testado com Playwright contra a API real.

**Tech Stack:** HTML5, CSS, JavaScript ES2022 modules · Node 24 `node:test` · `@playwright/test` (devDependency, Chrome do sistema).

**Spec:** `docs/superpowers/specs/2026-09-27-interface-operacao-design.md`

## Global Constraints

- Nenhuma alteração em `worker/api.py`, `worker/celery_app.py`, `worker/db.py`, `docker-compose.yml`, `worker/Dockerfile`.
- Nenhuma dependência de runtime no frontend; única devDependency: `@playwright/test`, em `tests-web/`.
- Dados da API entram no DOM só via `textContent`/atributos; `innerHTML` proibido com dados da API.
- Sem fontes, scripts ou imagens de terceiros; logos de `worker/web/assets/`.
- Textos da interface em português do Brasil.
- Rótulos: `queued`→"Na fila", `started`→"Em execução", `completed`→"Concluído", `failed`→"Falhou", `unknown`→"Desconhecido".
- Upload: extensão `.geojson` (sem distinção de maiúsculas), tamanho > 0 e ≤ 10 MB (10 485 760 bytes).
- Polling do job: 2 000 ms; histórico: 5 000 ms; backoff em erro dobrando até 15 000 ms.
- Sem git neste repositório (não inicializado): passos de commit não se aplicam.

## Review Focus

1. Duplo clique em "Processar" → exatamente 1 `POST /jobs/async` (botão desabilitado durante envio). Teste: Task 4, `envio unico com duplo clique`.
2. Trocar de job selecionado enquanto outro é consultado → resposta atrasada do job anterior não sobrescreve o detalhe. Teste: Task 1, `isCurrent` (usado por `app.js` antes de renderizar) + Task 4, `selecionar job do historico`.
3. Worker parado (job nunca termina) → polling segue a 2 s, sem laço acelerado. Teste: Task 3, `continua no intervalo enquanto task retorna true`.
4. Erro com corpo não-JSON (ex.: 502 HTML) → mensagem "HTTP 502", sem exceção não tratada. Teste: Task 2, `erro com corpo nao JSON`.
5. Status fora do conjunto conhecido → "Desconhecido" e polling continua. Teste: Task 1, `normalizeStatus desconhecido`.

---

### Task 0: Pré-voo do ambiente

**Files:** Create `tests-web/package.json`, `tests-web/playwright.config.mjs`, `tests-web/.gitignore`; Modify `.gitignore` (raiz: `tests-web/node_modules/`, `tests-web/test-results/`, `tests-web/playwright-report/`).

- [ ] **Step 1:** `docker compose restart api` (autorizado pelo usuário) e aguardar `/health`.
- [ ] **Step 2:** Verificar: `curl -s http://localhost:8000/openapi.json` contém `/jobs/{task_id}/files/{kind}`; `curl -o NUL -w "%{http_code}" http://localhost:8000/jobs/<task_id concluído>/files/mapa` → `200`.
- [ ] **Step 3:** `tests-web/package.json`: `"private": true`, `"type": "module"`, scripts `"test:unit": "node --test unit/"`, `"test:e2e": "playwright test"`; `npm install -D @playwright/test` em `tests-web/`.
- [ ] **Step 4:** `playwright.config.mjs`: `testDir: "e2e"`, `use.baseURL: process.env.GEOLUME_URL ?? "http://localhost:8000"`, `use.channel: "chrome"`, `timeout: 120_000`, `workers: 1`, projeto único `chrome-desktop` (1440×900).
- [ ] **Step 5:** `npx playwright test --list` executa sem erro (0 testes).

### Task 1: `model.js` — lógica pura

**Files:** Create `worker/web/js/model.js`; Test `tests-web/unit/model.test.mjs`.

**Interfaces — Produces:**
- `normalizeStatus(raw: unknown): "queued"|"started"|"completed"|"failed"|"unknown"` — `queued|pending|received|retry`→queued; `started`→started; `completed|success`→completed; `failed|failure|revoked`→failed; resto→unknown. Case-insensitive.
- `statusLabel(status: string): string` — rótulos da Global Constraints.
- `isTerminal(status: string): boolean` — true só para completed/failed.
- `fileLinks(taskId: string): Array<{kind, filename, href}>` — ordem mapa, memorial, resultado; `href = "/jobs/" + encodeURIComponent(taskId) + "/files/" + kind`.
- `summarize(jobs: Array<{status}>): {queued, started, completed, failed}` — usa `normalizeStatus`.
- `validateUpload(file: {name, size} | null): {ok: true} | {ok: false, message: string}` — mensagens: `"Selecione um arquivo .geojson"`, `"O arquivo está vazio"`, `"O arquivo excede 10 MB"`.
- `formatBytes(n: number): string` — `"512 B"`, `"12,3 KB"`, `"1,5 MB"` (pt-BR, 1 casa).
- `isCurrent(selectedTaskId: string|null, responseTaskId: string): boolean`.

- [ ] **Step 1:** Escrever testes: `normalizeStatus` para cada entrada acima + `"PENDING"`→queued + `"xyz"`/`null`→unknown (`normalizeStatus desconhecido`); `statusLabel("failed") === "Falhou"`; `isTerminal("started") === false`; `fileLinks("a/b c")[0].href === "/jobs/a%2Fb%20c/files/mapa"` e `filename` `mapa.pdf`/`memorial.pdf`/`resultado.json`; `summarize([{status:"completed"},{status:"pending"},{status:"failure"}])` → `{queued:1,started:0,completed:1,failed:1}`; `validateUpload` para `null`, `x.json`, `X.GEOJSON` 10 B (ok), 0 B, 10 485 761 B, 10 485 760 B (ok); `formatBytes(512)`, `(12595)`, `(1572864)`; `isCurrent("a","b") === false`.
- [ ] **Step 2:** `cd tests-web; npm run test:unit` → FAIL (módulo inexistente).
- [ ] **Step 3:** Implementar `worker/web/js/model.js` com os `export` acima.
- [ ] **Step 4:** `npm run test:unit` → todos PASS.

### Task 2: `api.js` — cliente HTTP

**Files:** Create `worker/web/js/api.js`; Test `tests-web/unit/api.test.mjs`.

**Interfaces — Produces:**
- `class ApiError extends Error { status: number }` — `status 0` = rede.
- `createApi(fetchImpl = globalThis.fetch.bind(globalThis))` → `{ health(), enqueue(file: Blob & {name}), getJob(taskId), listJobs(limit = 20) }`, cada um `Promise<object>` com o JSON.
  - `enqueue`: `POST /jobs/async`, `FormData` campo `file`.
  - `getJob`: `GET /jobs/${encodeURIComponent(taskId)}`.
  - `listJobs`: `GET /jobs?limit=${limit}`, retorna o array `jobs` (ou `[]`).
  - Resposta não-ok: `ApiError` com `message = detail` (se string) senão `"HTTP " + status`. `fetch` rejeitado: `ApiError("API indisponível", 0)`.

- [ ] **Step 1:** Testes com `fetch` falso registrando chamadas: URL e método de cada função; `listJobs()` retorna array; `getJob("a/b")` usa `/jobs/a%2Fb`; 400 com `{"detail":"A PoC aceita somente arquivos .geojson"}` → `ApiError` com essa mensagem e `status 400`; `erro com corpo nao JSON` (502, corpo `<html>`) → mensagem `"HTTP 502"`; fetch rejeitado → `status 0`, `"API indisponível"`.
- [ ] **Step 2:** `npm run test:unit` → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `npm run test:unit` → PASS.

### Task 3: `poller.js` — agendamento

**Files:** Create `worker/web/js/poller.js`; Test `tests-web/unit/poller.test.mjs`.

**Interfaces — Produces:**
- `createPoller({ task: () => Promise<boolean>, interval: number, maxInterval: number, timers = {setTimeout, clearTimeout} })` → `{ start(), stop(), get running() }`.
  - `start()` executa `task` imediatamente (idempotente se já rodando). `true` → reagenda em `interval`; `false` → para; exceção → reagenda em `min(atraso*2, maxInterval)`; sucesso seguinte volta a `interval`. `stop()` cancela o timer pendente e ignora conclusão em andamento.

- [ ] **Step 1:** Testes com timers falsos: `continua no intervalo enquanto task retorna true` (3 ciclos, atrasos `[2000,2000,2000]`); para quando `false`; backoff `[4000,8000,15000,15000]` e volta a 2000 após sucesso (interval 2000, max 15000); `stop()` durante task pendente não reagenda; `start()` duplo não duplica timers.
- [ ] **Step 2:** `npm run test:unit` → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `npm run test:unit` → PASS.

### Task 4: Tela — HTML, CSS, render e wiring (frontend-design)

**Files:** Modify (reescrever) `worker/web/index.html`; Create `worker/web/styles.css`, `worker/web/js/render.js`, `worker/web/js/app.js`; Test `tests-web/e2e/operacao.spec.mjs`.

**Interfaces — Consumes:** Tasks 1–3. **Produces (contrato do E2E, `data-testid`):** `health`, `upload-input` (input file), `upload-drop`, `upload-file`, `upload-submit`, `upload-message`, `detail`, `detail-job-id`, `detail-task-id`, `detail-status`, `detail-error`, `detail-links` (âncoras com `data-kind`), `summary`, `jobs-table`, `jobs-row` (com `data-task-id`, clicável e acionável por teclado), `jobs-empty`, `jobs-error`, `jobs-refresh`.

- [ ] **Step 1:** Escrever E2E (API real) — cada um espera condições, nunca `sleep` fixo:
  - `abre a tela`: título contém "GeoLume"; logo `img[src$="geolume-logo-transparente.png"]` visível com `naturalWidth > 0`; `health` contém "API online".
  - `envia GeoJSON e acompanha ate concluir`: `setInputFiles` com `worker/tests/fixtures/gleba_rural_exemplo.geojson`; `upload-file` mostra nome; clicar `upload-submit`; `detail-job-id` casa `/^[0-9a-f]{32}$/`; `detail-task-id` casa UUID; `detail-status` passa a "Concluído" (timeout 90 s); a resposta de `GET /jobs/{task_id}` foi observada ao menos 1 vez.
  - `links dos arquivos`: `detail-links a` tem 3 itens `mapa.pdf`, `memorial.pdf`, `resultado.json`; `request.get(href)` → 200 com `content-type` `application/pdf`, `application/pdf`, `application/json`.
  - `historico lista o job`: `jobs-row[data-task-id="<id>"]` existe e mostra "Concluído"; `summary` reflete contagem de `GET /jobs`.
  - `job com falha`: envia `worker/tests/fixtures/autointersecao.geojson` → "Falhou" e `detail-error` não vazio.
  - `selecionar job do historico`: clicar outra `jobs-row` → `detail-task-id` igual ao `data-task-id` clicado.
  - `escape de nome malicioso`: `setInputFiles({name: '<img src=x onerror="window.__xss=1">.geojson', mimeType: 'application/geo+json', buffer: <conteúdo de lote_simples>})`; após listar, `window.__xss` é `undefined` e o texto literal aparece na linha.
  - `valida extensao`: arquivo `dados.json` → `upload-message` "Selecione um arquivo .geojson", nenhum `POST /jobs/async`.
  - `envio unico com duplo clique`: `dblclick` no submit → exatamente 1 `POST /jobs/async`.
  - `api indisponivel`: `page.route("**/jobs?**", r => r.abort())` → `jobs-error` visível com botão tentar novamente; `page.route("**/health", r => r.fulfill({status: 500}))` → `health` "API indisponível".
  - `sem erros no console`: nenhuma mensagem `error` durante o fluxo feliz.
- [ ] **Step 2:** `npm run test:e2e` → FAIL (testids inexistentes na tela atual).
- [ ] **Step 3:** Invocar `frontend-design`; implementar `index.html` (estrutura da spec, `lang="pt-BR"`, `<script type="module" src="/js/app.js">`), `styles.css` (tokens da spec, sem gradiente/sombra decorativa, breakpoints 1024 e 640 px, `prefers-reduced-motion`), `render.js` (funções que recebem elementos e dados, só `createElement`/`textContent`), `app.js` (estado: `selectedTaskId`; poller do job 2 s parado ao ficar terminal ou ao trocar seleção; poller da lista 5 s; `visibilitychange` para/retoma; `isCurrent` descarta respostas antigas; botões de copiar com `navigator.clipboard`).
- [ ] **Step 4:** `npm run test:e2e` → PASS; `npm run test:unit` → PASS.

### Task 5: Responsividade

**Files:** Modify `tests-web/e2e/operacao.spec.mjs`, `worker/web/styles.css` se necessário.

- [ ] **Step 1:** Teste `layout sem rolagem horizontal` para viewports 1440×900, 768×1024, 375×812: `document.documentElement.scrollWidth <= innerWidth`; em 375 `upload-submit` e primeira `jobs-row` visíveis sem rolagem lateral; em 1440 `detail` e `jobs-table` lado a lado (boxes com `x` distintos).
- [ ] **Step 2:** `npm run test:e2e` → PASS (corrigir CSS se falhar).

### Task 6: Verificação no navegador (Playwright MCP + Chrome DevTools MCP)

- [ ] **Step 1:** Playwright MCP: abrir `http://localhost:8000`, upload real, acompanhar até concluir, screenshots desktop/mobile.
- [ ] **Step 2:** Chrome DevTools MCP: `list_console_messages` sem `error`; `list_network_requests` mostra `GET /health`, `GET /jobs?limit=20`, `POST /jobs/async` 202, `GET /jobs/{task_id}` 200, downloads 200; `emulate` 375/768/1440 com screenshots; auditoria Lighthouse de acessibilidade.

### Task 7: Documentação e revisão final

**Files:** Modify `worker/README.md` (seção "Interface web": subir stack, abrir `http://localhost:8000`, rodar testes em `tests-web/`).

- [ ] **Step 1:** Atualizar README.
- [ ] **Step 2:** `superpowers:verification-before-completion`: rodar unit + e2e e registrar saída real.
- [ ] **Step 3:** `superpowers:requesting-code-review`: revisor independente sobre `worker/web/` e `tests-web/`; corrigir achados relevantes e rodar os testes de novo.

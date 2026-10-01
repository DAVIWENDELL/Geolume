# GeoLume — Interface de operação (Monitor/Operate) — Design

Data: 2026-09-27 · Status: aguardando aprovação

## Objetivo

Primeira tela real do GeoLume para operar o processamento geográfico: enviar um
GeoJSON, acompanhar o job até o fim e baixar `mapa.pdf`, `memorial.pdf` e
`resultado.json`. Tela de operação, não de marketing.

## Estado atual (verificado em 2026-09-27)

- `worker/web/index.html` já existe e é servido pelo FastAPI em `/`
  (`app.mount("/", StaticFiles(directory="/app/web", html=True))`). HTML/CSS/JS
  puro, sem build. Faz upload para `POST /jobs/async`, lista `GET /jobs` a cada
  5 s e monta links `/jobs/{task_id}/files/{mapa|memorial|resultado}`.
- Lacunas: não mostra `job_id`/`task_id`; não usa `GET /jobs/{task_id}`; falha
  silenciosa ao listar; sem estados de loading/erro; itens sem função
  (menu "Propriedades", avatar); paleta sem azul claro e marrom.
- A rota de download existe no código (`api.py:110`), mas o processo `api` em
  execução é anterior a ela (ausente do `openapi.json` ao vivo) → 404.
  Correção: `docker compose restart api`. Nenhuma mudança de código.
- `GET /jobs/{id}` desconhecido → `200 {"status":"pending"}` (fallback Celery).

## Decisões

| # | Decisão | Motivo |
|---|---|---|
| D1 | Evoluir a tela existente em `worker/web/` (mesma URL, mesmo `StaticFiles`) | Preserva a arquitetura; frontend servido pela própria API, sem CORS nem novo serviço |
| D2 | Manter HTML/CSS/JS puro com ES modules nativos, sem framework nem build | Stack já existente; 1 tela não justifica React/Vite; zero dependência em runtime |
| D3 | Separar em módulos: `model.js` (lógica pura), `api.js`, `poller.js`, `render.js`, `app.js`, `styles.css` | Lógica pura testável sem DOM; arquivo único atual já mistura tudo |
| D4 | Testes unitários com `node:test` (embutido no Node 24) | Sem dependência para testar lógica pura |
| D5 | E2E com `@playwright/test` (única devDependency), usando o Chrome instalado (`channel: "chrome"`) | Testes repetíveis de navegador; evita baixar navegadores. Fica em `tests-web/` na raiz, fora da imagem Docker |
| D6 | E2E contra a API real em `localhost:8000`; interceptação de rede só para simular API fora do ar / HTTP 500 | Requisito "sem dados falsos"; falhas de infraestrutura não são reproduzíveis de forma real e segura |
| D7 | Estado `failed` testado com fixture real inválida (`worker/tests/fixtures/autointersecao.geojson`) | Falha real do worker, sem mock |
| D8 | Renderização só com `createElement`/`textContent`; `innerHTML` proibido para dados da API; `task_id` codificado com `encodeURIComponent` nos links | Escape seguro por construção; testado com nome de arquivo `<img src=x onerror=...>.geojson` enviado de verdade |
| D9 | Polling: job ativo a cada 2 s via `GET /jobs/{task_id}` até `completed`/`failed`; lista a cada 5 s e imediatamente na transição de estado; pausa com aba oculta; backoff até 15 s em erro | Acompanhamento responsivo sem martelar a API |
| D10 | Estado `pending` (fallback Celery) exibido como "Na fila" | Evita estado desconhecido na UI |
| D11 | Upload em 2 passos: selecionar/arrastar → validar no cliente (extensão `.geojson`, não vazio, ≤ 10 MB) → botão "Processar" | Operação explícita; validações baratas antes de ir à API. Validação geométrica continua no worker |
| D12 | Selecionar uma linha do histórico abre esse job no painel de detalhe (ids, estado, erro, links) | Jobs anteriores também operáveis, não só listados |
| D13 | Remover itens sem função (menu fictício, avatar) e substituir por indicador real de saúde da API (`GET /health`) | Requisito: nada decorativo sem função |
| D14 | Fontes do sistema; IDs em monoespaçada com números tabulares; sem fonte externa | Nenhuma requisição a terceiros; IDs legíveis e copiáveis |
| D15 | Sem alteração em `api.py`, `celery_app.py`, `db.py`, compose ou Dockerfile | Requisito de preservar worker/Redis/Celery/PostgreSQL |
| D16 | `web/assets/` da raiz (cópia idêntica) não é tocada | Fora do escopo; apenas registrado |

## Identidade visual

Cores amostradas da logo:

| Token | Uso |
|---|---|
| `--verde-escuro` `#0b4a32` | Barra superior, títulos, texto forte |
| `--verde-petroleo` `#047a5e` | Ações secundárias, links, foco |
| `--verde-medio` `#3aa655` | Estado `completed` |
| `--laranja` `#d2772a` | Ação primária "Processar", estado `started` |
| `--azul-claro` `#8fd3f0` | Estado `queued`, zona de upload ativa |
| `--marrom` `#5a3519` | Somente elementos cartográficos: moldura (neatline) dos documentos gerados e régua de escala do painel de saída |
| `--erro` `#b3261e` | Estado `failed` e mensagens de erro (semântica, fora da paleta da logo) |

Sem gradientes, glassmorphism, sombras decorativas ou cards sem função.
Superfícies planas com bordas de 1 px.

## Layout (Monitor/Operate)

```
┌──────────────────────────────────────────────────────────────┐
│ [logo]  Centro de operação              ● API online          │
├──────────────────────┬───────────────────────────────────────┤
│ NOVO PROCESSAMENTO   │ HISTÓRICO  (últimos 20)   [Atualizar]  │
│ [zona de upload]     │ contagem: fila · exec · concl · falha  │
│ arquivo.geojson 12KB │ ┌───────────────────────────────────┐  │
│ [Processar]          │ │ arquivo | estado | criado | docs  │  │
├──────────────────────┤ │ ...  (linha clicável → detalhe)   │  │
│ JOB SELECIONADO      │ └───────────────────────────────────┘  │
│ job_id  [copiar]     │                                        │
│ task_id [copiar]     │                                        │
│ fila → exec → concl  │                                        │
│ erro (se failed)     │                                        │
│ [mapa.pdf][memorial] │                                        │
│ [resultado.json]     │                                        │
└──────────────────────┴───────────────────────────────────────┘
```

- ≥ 1024 px: duas colunas (operar à esquerda, monitorar à direita).
- < 1024 px: uma coluna — upload, job selecionado, histórico.
- < 640 px: a tabela vira lista de linhas empilhadas (sem rolagem horizontal).

## Estados tratados

| Área | Loading | Vazio | Erro | Sucesso |
|---|---|---|---|---|
| Saúde | "Verificando" | — | "API indisponível" | "API online" |
| Upload | botão "Enviando…" desabilitado | "Nenhum arquivo selecionado" | mensagem inline (validação ou HTTP 4xx/5xx com `detail`) | job aberto no painel com ids |
| Job | "Consultando" | "Nenhum job selecionado" | aviso de falha de consulta mantendo último estado conhecido | estado + links |
| Histórico | linha "Carregando…" | "Nenhum processamento ainda" | faixa de erro + botão tentar novamente | tabela |

Regiões dinâmicas com `aria-live="polite"`; estados com texto além da cor.

## Testes

- Unitários (`node:test`): `model.js` (normalização, rótulos, terminal,
  links, contagem, validação de upload), `api.js` (tratamento de respostas com `fetch` injetado),
  `poller.js` (agendamento, parada em terminal, backoff, pausa).
- E2E (`@playwright/test`, API real): abertura da tela; upload do GeoJSON;
  exibição de `job_id`/`task_id`; progressão até `completed`; progressão até
  `failed` com mensagem; histórico contém o job; links retornam 200 com
  `content-type` correto; escape de nome malicioso; validação de extensão;
  API fora do ar (interceptado); layout em 1440, 768 e 375 px sem rolagem
  horizontal.
- Verificação com Chrome DevTools MCP: console sem erros, requisições HTTP
  esperadas, layout responsivo.

## Fora do escopo

Autenticação, multi-tenancy, mapa interativo, cancelamento de job, paginação
além de 20 itens, mudanças no backend.

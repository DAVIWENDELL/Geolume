# GeoLume — Autenticação e autorização dos jobs — Design

Data: 2026-09-28 · Status: aprovado em conversa; spec aguardando revisão

## Objetivo

Impedir que quem conhece um `task_id` consulte dados de outro usuário ou baixe
`mapa.pdf`, `memorial.pdf`, `resultado.json` e o GeoJSON original. A autorização
vive no backend; a interface só reage a ela.

## Estado atual (verificado em 2026-09-28)

- Nenhuma autenticação. 144 jobs no banco (114 `completed`, 30 `failed`), sem dono.
- Tabelas públicas: `jobs`, `spatial_ref_sys`. Migração em `init_db`, sob
  `pg_advisory_lock(731942)`.
- Imagem: FastAPI 0.101 / Starlette 0.31 via apt; sem argon2, bcrypt, PyJWT,
  passlib, itsdangerous, authlib ou httpx. `hashlib.scrypt` funciona.
- Downloads na UI são `<a href download>`: não enviam header, só cookie.

Auditoria (Claude + Codex, somente leitura):

| Achado | Local | Severidade |
|---|---|---|
| `GET /jobs` lista jobs de todos, com `task_id` | `api.py:88`, `db.py:101` | Crítico |
| Com o `task_id`: status, 3 downloads e GeoJSON original, sem dono | `api.py:93,115,148` | Crítico |
| `jobs` sem `owner_id`/`tenant_id` | `db.py:17,64` | Crítico |
| Fallback `AsyncResult` em `GET /jobs/{task_id}` devolve estado e resultado do Celery para qualquer id fora do banco — contornaria qualquer filtro | `api.py:106` | Crítico |
| `POST /jobs/async` e `POST /jobs` (QGIS no processo da API) sem autenticação | `api.py:45,71` | Importante |
| Upload copiado inteiro antes do limite de 10 MB | `api.py:54,80` | Importante (fora deste escopo) |
| `erro` = `str(exc)` exposto | `celery_app.py:46`, `api.py:104` | Menor (fora deste escopo) |
| Porta 8000 publicada em todas as interfaces | `docker-compose.yml` | Menor (fora deste escopo; exige compose) |

Corretos hoje: `StaticFiles` só serve `/app/web`; SQL parametrizado; UI sem
`innerHTML` com dados; Redis/Postgres não publicados no host.

## Opções avaliadas

| | Sessão própria (escolhida) | JWT access + refresh | IdP externo (OIDC) |
|---|---|---|---|
| Dependências | nenhuma (stdlib) | PyJWT → muda Dockerfile | authlib/httpx + provedor → Dockerfile e compose |
| `<a href download>` | funciona (cookie) | não envia `Authorization`; exigiria blob ou cookie | só via sessão local |
| Logout/revogação | imediatos (apaga linha) | lista de revogação + rotação | depende do provedor + sessão local |
| Complexidade | baixa | média | alta (state, nonce, PKCE, JWKS) |

O IdP entra no futuro sem refazer a sessão: o callback OIDC termina criando a
mesma linha em `sessions` (colunas `auth_provider`/`external_subject` em `users`
nessa etapa).

## Decisões

| # | Decisão |
|---|---|
| D1 | Sessão própria: token opaco em cookie, sessões e usuários no PostgreSQL, senha com `hashlib.scrypt` |
| D2 | Jobs antigos ficam com `owner_id NULL` |
| D3 | Administrador vê e acessa todos os jobs, inclusive os legados |
| D4 | Usuário comum vê e acessa só os próprios jobs; nunca os legados |
| D5 | `POST /jobs` síncrono: só administrador |
| D6 | `POST /jobs/async`: qualquer usuário autenticado |
| D7 | Sessão expira após 2 h sem uso e no máximo 12 h após o login |
| D8 | Demonstração só em `localhost`; acesso por IP da rede fica para a etapa com HTTPS. Cookie sempre `Secure` (navegadores tratam `http://localhost` como origem segura) |
| D9 | Job inexistente e job de outro usuário: o mesmo `404 "Job não encontrado"`. Nunca 403 |
| D10 | Fallback `AsyncResult` removido: o Celery deixa de ser fonte de dados para o cliente |
| D11 | Sem cadastro público: usuários criados por CLI dentro do contêiner, senha lida do stdin |
| D12 | Não mudam: `docker-compose.yml`, `Dockerfile`, `celery_app.py`, `geo.js`, `map.js`, o volume PostgreSQL |

## Modelo de dados

Migração idempotente no `SCHEMA` de `init_db`, sob o advisory lock existente.
Nada é apagado.

```sql
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO tenants (id, nome) VALUES ('demo', 'GeoLume (demo)') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,                       -- uuid4 hex
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    email TEXT NOT NULL UNIQUE,                -- normalizado: strip + lower
    password_hash TEXT NOT NULL,               -- scrypt$n$r$p$salt_b64$hash_b64
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    active BOOLEAN NOT NULL DEFAULT true,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TIMESTAMPTZ,
    password_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,               -- sha256 hex do token; o token nunca é gravado
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL            -- created_at + 12 h
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id TEXT REFERENCES tenants(id);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS owner_id TEXT REFERENCES users(id);
CREATE INDEX IF NOT EXISTS jobs_tenant_owner_idx ON jobs (tenant_id, owner_id, created_at DESC);
```

Backfill idempotente: `UPDATE jobs SET tenant_id = 'demo' WHERE tenant_id IS NULL`.
`owner_id` dos jobs existentes continua `NULL` (D2).

## Senhas

- `hashlib.scrypt`, n=2^15, r=8, p=3 (parâmetro OWASP), salt de 16 bytes de
  `secrets`, `dklen=32`, `maxmem=64 MiB`. Parâmetros gravados no próprio hash,
  para permitir endurecer depois sem invalidar senhas.
- Comparação com `hmac.compare_digest`.
- Senha mínima de 12 caracteres (validada no CLI).
- Nunca em texto puro: nem no banco, nem em log, nem em código, compose ou
  histórico do shell.

## Sessão

- Login gera sempre um token novo `secrets.token_urlsafe(32)` (sem fixação de
  sessão); o banco guarda só `sha256(token)`.
- Cookie `geolume_session`: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, sem
  `Domain`, `Max-Age` = 12 h.
- Válida se `now < expires_at` e `now - last_seen_at < 2 h`, usuário `active`.
  Expirada → linha apagada, 401.
- `last_seen_at` atualizado no máximo 1 vez por minuto.
- Logout apaga a linha e o cookie. Troca de senha e desativação apagam todas as
  sessões do usuário. Sessões vencidas são limpas a cada login.

## Endpoints

| Método e rota | Acesso |
|---|---|
| `GET /health`, arquivos estáticos | público |
| `POST /auth/login` | público, com CSRF |
| `POST /auth/logout` | autenticado, com CSRF |
| `GET /auth/me` | autenticado → `{email, role}` |
| `GET /jobs` | autenticado; filtrado (D3/D4) |
| `GET /jobs/{task_id}` | autenticado; filtrado; sem `AsyncResult` |
| `GET /jobs/{task_id}/files/{kind}` | autenticado; filtrado |
| `GET /jobs/{task_id}/input` | autenticado; filtrado |
| `POST /jobs/async` | autenticado, com CSRF; grava `owner_id` e `tenant_id` antes de enfileirar |
| `POST /jobs` | administrador, com CSRF |

**Autorização.** Uma dependência `current_user` resolve o cookie em
`{id, tenant_id, role}` ou responde 401. Toda leitura de job passa por uma única
consulta já filtrada — nunca busca global seguida de comparação:

```sql
SELECT * FROM jobs
WHERE task_id = %s AND tenant_id = %s
  AND (%s = 'admin' OR owner_id = %s)
```

Sem linha → `404 "Job não encontrado"`, igual para inexistente e alheio (D9). O
mesmo filtro vale para a listagem. A checagem acontece antes de qualquer acesso
ao disco; a validação de caminho do GeoJSON continua como está.

Um teste percorre `app.routes` e falha se alguma rota `/jobs*` ou `/auth/logout`
ficar sem a dependência — nova rota não nasce aberta.

**Login.**
- Resposta de erro sempre `401 "E-mail ou senha inválidos"`, para e-mail
  inexistente, senha errada, usuário inativo ou bloqueado.
- E-mail inexistente ainda calcula um scrypt sobre um hash fixo (tempo igual).
- 5 falhas seguidas → `locked_until = now + 15 min`; sucesso zera o contador.

**CSRF.** Todo `POST` exige o header `X-GeoLume-CSRF: 1` e, se houver `Origin`,
ele deve ser igual à origem do servidor. Sem CORS configurado, outro site não
consegue enviar esse header; `SameSite=Lax` impede o envio do cookie em POST
cross-site. Falha → 403. `GET` nunca altera estado.

**Sem autenticação.** API responde `401 "Autenticação necessária"`. A página e os
estáticos seguem públicos e exibem só o formulário de login.

## Interface

- `index.html` ganha uma seção de login (e-mail, senha, botão Entrar, mensagem
  `aria-live`) e, no cabeçalho, o e-mail do usuário e o botão Sair. A área de
  operação fica oculta até `GET /auth/me` responder 200.
- `api.js`: `login`, `logout`, `me`; todo `POST` envia `X-GeoLume-CSRF: 1`;
  `ApiError` 401 dispara um único callback `onUnauthorized`.
- `app.js`: ao receber 401 ou sair, para todos os pollers e limpa **todo** o
  estado (`shapes`, `inputs`, `metrics`, job selecionado, histórico, mapa) antes
  de mostrar o login — o próximo usuário não vê nada do anterior.
- DOM seguro: só `textContent`/`createElement`.

## Administrador e CLI

`worker/scripts/users.py`, executado com
`docker compose exec api python3 scripts/users.py <comando>`:

- `create --email X --role admin|member` — senha lida por `getpass` (TTY) ou
  pela primeira linha do stdin (`--password-stdin`, usado pelos testes).
- `reset-password --email X` — mesma leitura; apaga as sessões do usuário.
- `disable --email X` — `active = false`; apaga as sessões.

O administrador da demonstração é criado pelo usuário com esse comando. Nenhuma
senha no código, no compose ou em argumentos de linha de comando.

## Futuro (fora deste escopo, já acomodado)

- **Recuperação de senha:** tabela `password_reset_tokens` (hash do token,
  validade 30 min, uso único) + provedor de e-mail; ao redefinir, apagar as
  sessões. Até lá, `reset-password` pelo CLI.
- **Multi-tenancy:** `tenant_id` já está em `users`, `jobs` e em todos os
  filtros; um cliente novo vira um tenant novo. O papel `admin` é do tenant —
  com um único tenant, vê todos os jobs (D3). Um papel de plataforma e RLS no
  PostgreSQL entram quando houver mais de um tenant real.
- **IdP externo:** ver "Opções avaliadas".
- **Etapa HTTPS:** acesso pela rede, porta 8000 restrita a `127.0.0.1`.
- **Endurecimento separado:** limite de 10 MB durante a cópia do upload;
  mensagens de erro sem `str(exc)`.

## Testes (TDD: vermelho antes de cada implementação)

pytest (contêiner `worker`, banco simulado como hoje):
- scrypt: gera e verifica; senha errada falha; hash não contém a senha; parâmetros lidos do hash.
- sessão: válida; vencida por inatividade (2 h); vencida pelo prazo máximo (12 h); usuário inativo.
- login: sucesso define o cookie com os atributos; erro genérico igual nos 4 casos; bloqueio após 5 falhas; token novo a cada login.
- logout apaga a sessão; troca de senha apaga as sessões.
- 401 sem cookie em `GET /jobs`, `GET /jobs/{id}`, `/files/*`, `/input`, `POST /jobs/async`, `POST /jobs`.
- usuário A → 404 em status, 3 downloads e input dos jobs de B; listagem de A sem jobs de B.
- membro → 404 em job legado; admin → 200 em job legado e em job de membro.
- membro → 403 em `POST /jobs` síncrono.
- CSRF: POST sem header → 403; `Origin` diferente → 403.
- `AsyncResult` nunca é chamado (id fora do banco → 404).
- migração: tabelas e colunas com `IF NOT EXISTS`, backfill de `tenant_id` só onde é `NULL`, tudo dentro do lock.
- toda rota `/jobs*` tem a dependência de autenticação.

JS unitário: `api.js` envia o header CSRF nos POST; 401 chama `onUnauthorized` uma vez; `login`/`logout`/`me`.

Playwright (desktop 1440 e mobile 375, API real): um `globalSetup` cria
`e2e-admin`, `e2e-a` e `e2e-b` com senhas geradas a cada execução, passadas pelo
stdin do CLI e gravadas em `tests-web/.auth/` (ignorado pelo Git). Casos:
- login, logout, senha errada;
- A não vê o job de B no histórico, e acessar a URL de B responde 404;
- admin vê os jobs legados; membro não;
- sessão revogada no meio do uso volta ao login e limpa o mapa;
- todos os specs atuais rodam autenticados, sem perder cobertura.

Validação final: Chrome DevTools (console, rede, cookies); Codex como revisor
independente, somente leitura, depois da implementação.

## Critérios de aceite

- Nenhum dos 5 recursos de um job é obtido sem login ou por outro usuário comum.
- Os 144 jobs existentes continuam acessíveis ao administrador, com mapa e downloads.
- Nenhuma senha em texto puro em lugar algum.
- pytest, JS unitário e Playwright passando, com os resultados reais mostrados.
- Volume PostgreSQL, QGIS, Celery, Redis e PostGIS preservados.

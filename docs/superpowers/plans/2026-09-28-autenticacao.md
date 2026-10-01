# Autenticação e autorização dos jobs — Plano de implementação

> **Para quem executa:** usar superpowers:executing-plans (ou subagent-driven-development)
> tarefa a tarefa. Passos com `- [ ]`. O projeto não é repositório git: sem passos de commit.

**Objetivo:** só o dono (ou o administrador) acessa status, downloads e GeoJSON de um job.

**Arquitetura:** funções puras de senha/sessão em `worker/auth.py`; consultas
filtradas por usuário em `worker/db.py`; dependências FastAPI `current_user`,
`require_admin` e `check_csrf` em `worker/api.py`; CLI de usuários; tela de login
na UI existente. Nenhuma dependência nova.

**Stack:** Python 3.12 stdlib (`hashlib.scrypt`, `secrets`, `hmac`), FastAPI 0.101,
psycopg2, JS puro, `node:test`, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-28-autenticacao-design.md`

## Restrições globais

- Não apagar o volume PostgreSQL; migração só aditiva, idempotente, sob `pg_advisory_lock(731942)`.
- Não alterar `docker-compose.yml`, `Dockerfile`, `celery_app.py`, `geo.js`, `map.js`.
- Senha nunca em texto puro (banco, log, código, compose, argumentos de CLI, histórico do shell).
- Autorização no backend; job inexistente ou alheio → `404 "Job não encontrado"`.
- Sem fallback `AsyncResult`.
- Sessão: 2 h de inatividade, 12 h no máximo; cookie `geolume_session` `HttpOnly; Secure; SameSite=Lax; Path=/`.
- CSRF: todo POST exige `X-GeoLume-CSRF: 1` e `Origin` (se presente) igual à origem do servidor.
- TDD: rodar o teste novo e ver falhar antes de implementar. pytest roda com
  `docker compose run --rm worker python3 -m pytest -q > out.txt; echo exit=$?`
  (nunca `| tail`, que mascara o exit code).
- UI: só `textContent`/`createElement`. `app.js` e `render.js` são CRLF.
- Codex só depois da implementação, somente leitura.

## Foco da revisão

1. Outro usuário entra na mesma aba depois de um logout ou 401 → não vê mapa, métricas nem histórico do anterior (E2E na Tarefa 8).
2. Cookie `Secure` em `http://localhost` no Chrome e no `request` do Playwright (verificado na Tarefa 8 e no DevTools, Tarefa 9). Se o Playwright não reenviar, **parar e reportar** — não remover `Secure`.
3. E-mail com maiúsculas ou espaços no login → mesmo usuário (Tarefa 1, `normalize_email`; Tarefa 3, teste de login).
4. Usuário desativado ou senha trocada com sessão aberta → próxima requisição 401 (Tarefa 3).
5. `api` e `celery` rodando `init_db` ao mesmo tempo → migração sob o lock, sem erro (Tarefa 2; verificado ao reiniciar, Tarefa 9).

---

### Tarefa 1: senha e sessão (funções puras)

**Arquivos:** criar `worker/auth.py`, `worker/tests/test_auth.py`.

**Interfaces produzidas:**
- `IDLE_TIMEOUT = timedelta(hours=2)`, `MAX_AGE = timedelta(hours=12)`, `TOUCH_EVERY = timedelta(minutes=1)`, `MAX_FAILED_LOGINS = 5`, `LOCK_FOR = timedelta(minutes=15)`, `MIN_PASSWORD = 12`
- `normalize_email(email: str) -> str` — `strip().lower()`
- `hash_password(password: str) -> str` — `scrypt$32768$8$3$<salt b64>$<hash b64>`, salt 16 bytes, dklen 32, maxmem 64 MiB
- `verify_password(password: str, encoded: str) -> bool` — lê n/r/p do próprio hash; formato inválido → `False`; `hmac.compare_digest`
- `burn_password_check(password: str) -> None` — verifica contra um hash fixo calculado uma vez (tempo igual para e-mail inexistente)
- `new_session_token() -> tuple[str, str]` — `(token, hash_token(token))`, token `secrets.token_urlsafe(32)`
- `hash_token(token: str) -> str` — sha256 hex
- `session_is_valid(*, last_seen_at, expires_at, now) -> bool` — `now < expires_at and now - last_seen_at < IDLE_TIMEOUT`

- [ ] **Passo 1: testes que falham** em `test_auth.py`:
  - `test_hash_verifica_a_senha_certa_e_recusa_a_errada`;
  - `test_hash_nao_contem_a_senha_e_usa_salt_diferente` (dois hashes da mesma senha diferem);
  - `test_verifica_com_parametros_gravados_no_hash` (hash montado com n=16384 verifica);
  - `test_hash_malformado_retorna_false`;
  - `test_token_tem_hash_sha256_e_nao_repete`;
  - `test_sessao_valida_ate_2h_sem_uso` (1h59 → True; 2h → False);
  - `test_sessao_expira_em_12h_mesmo_em_uso` (last_seen agora, expires no passado → False);
  - `test_normaliza_email` (`"  Ana@X.COM "` → `"ana@x.com"`).
- [ ] **Passo 2:** rodar `-k test_auth` → FAIL (`ModuleNotFoundError: auth`).
- [ ] **Passo 3:** implementar `worker/auth.py` com as assinaturas acima.
- [ ] **Passo 4:** rodar `-k test_auth` → PASS.

### Tarefa 2: migração e consultas

**Arquivos:** modificar `worker/db.py`, `worker/tests/test_db.py`.

**Interfaces consumidas:** nenhuma. **Produzidas:**
- SCHEMA com `tenants`, `users`, `sessions`, colunas `jobs.tenant_id`/`jobs.owner_id` e índice, exatamente como na spec (seção Modelo de dados), com `INSERT ... ON CONFLICT DO NOTHING` do tenant `demo`; `BACKFILL_TENANT = "UPDATE jobs SET tenant_id = 'demo' WHERE tenant_id IS NULL"` executado no lock, depois do backfill de `input_path`.
- `create_user(email: str, password_hash: str, role: str, tenant_id: str = "demo") -> str` (id uuid4 hex)
- `get_user_by_email(email: str) -> dict | None`
- `set_password(user_id: str, password_hash: str) -> None` e `set_active(user_id: str, active: bool) -> None` — ambos apagam as sessões do usuário na mesma transação
- `register_login_failure(user_id: str) -> None` — incrementa; ao atingir `MAX_FAILED_LOGINS`, `locked_until = now + LOCK_FOR` e zera o contador
- `reset_login_failures(user_id: str) -> None`
- `create_session(token_hash: str, user_id: str, now: datetime) -> None` — `expires_at = now + MAX_AGE`; antes, apaga as sessões vencidas (`expires_at <= now OR last_seen_at <= now - IDLE_TIMEOUT`)
- `get_session_user(token_hash: str) -> dict | None` — JOIN users: `user_id, email, tenant_id, role, active, last_seen_at, expires_at`
- `touch_session(token_hash: str, now: datetime) -> None`, `delete_session(token_hash: str) -> None`
- `create_job(job_id, filename, *, owner_id: str, tenant_id: str, task_id=None, input_path=None) -> None`
- `get_job_for(task_id: str, user: dict) -> dict | None` e `list_jobs_for(user: dict, limit: int = 20) -> list[dict]` — o filtro da spec (`tenant_id = %s AND (%s = 'admin' OR owner_id = %s)`), `user` com as chaves `user_id`, `tenant_id` e `role`
- **Removidas:** `get_job`, `list_jobs` (sem consulta global que possa escapar ao filtro).

- [ ] **Passo 1: testes que falham** (FakeConn existente):
  - `test_schema_cria_tabelas_de_auth_idempotente` (cada `CREATE`/`ALTER` com `IF NOT EXISTS`);
  - `test_backfill_de_tenant_so_onde_nulo_e_dentro_do_lock`;
  - `test_create_job_grava_dono_e_tenant`;
  - `test_get_job_for_filtra_por_tenant_e_dono` (SQL contém o filtro; params `(task_id, tenant, role, user_id)`);
  - `test_list_jobs_for_usa_o_mesmo_filtro`;
  - `test_set_password_e_set_active_apagam_sessoes`;
  - `test_create_session_expira_em_12h_e_limpa_vencidas`;
  - `test_db_nao_expoe_consulta_global` (`not hasattr(db, "get_job")`).
  - Ajustar os testes antigos de `create_job` para a nova assinatura.
- [ ] **Passo 2:** rodar `-k test_db` → FAIL.
- [ ] **Passo 3:** implementar.
- [ ] **Passo 4:** rodar `-k test_db` → PASS.

### Tarefa 3: login, logout, sessão e CSRF na API

**Arquivos:** modificar `worker/api.py`; criar `worker/tests/test_api_auth.py`.

**Consumidas:** Tarefas 1 e 2. **Produzidas:**
- `SESSION_COOKIE = "geolume_session"`, `CSRF_HEADER = "x-geolume-csrf"`
- `check_csrf(request: Request) -> None` — sem header `1` ou `Origin` ≠ `f"{scheme}://{host}"` → `403 "Requisição recusada"`
- `current_user(request: Request) -> dict` — sem cookie, sessão inexistente, inválida (`session_is_valid`) ou usuário inativo → `401 "Autenticação necessária"` (sessão inválida é apagada); toca `last_seen_at` se passou `TOUCH_EVERY`
- `require_admin(user = Depends(current_user)) -> dict` — `403 "Acesso restrito ao administrador"`
- `POST /auth/login` (body JSON `{"email", "password"}`, `Depends(check_csrf)`) → `200 {"email", "role"}` + `Set-Cookie`; qualquer falha → `401 "E-mail ou senha inválidos"`
- `POST /auth/logout` (`check_csrf`, `current_user`) → `204`, apaga a sessão e o cookie
- `GET /auth/me` → `{"email", "role"}`

Testes chamam as funções diretamente (sem httpx), com `starlette.requests.Request` montado a partir de um scope, e os `db.*` monkeypatchados.

- [ ] **Passo 1: testes que falham:**
  - `test_login_define_cookie_httponly_secure_lax_12h` (confere o header `set-cookie`);
  - `test_login_gera_token_novo_a_cada_vez`;
  - `test_login_erro_generico_igual` (e-mail inexistente, senha errada, inativo, bloqueado → mesmo 401; e-mail inexistente chama `burn_password_check`);
  - `test_login_normaliza_email`;
  - `test_quinta_falha_bloqueia` (chama `register_login_failure`); `test_sucesso_zera_falhas`;
  - `test_logout_apaga_sessao_e_cookie`;
  - `test_current_user_401_sem_cookie`, `..._sessao_expirada_apaga_linha`, `..._usuario_inativo`;
  - `test_current_user_toca_last_seen_so_apos_1min`;
  - `test_post_sem_header_csrf_403`, `test_post_origin_diferente_403`;
  - `test_require_admin_403_para_membro`.
- [ ] **Passo 2:** rodar `-k test_api_auth` → FAIL.
- [ ] **Passo 3:** implementar em `api.py`, antes do `app.mount`.
- [ ] **Passo 4:** rodar `-k test_api_auth` → PASS.

### Tarefa 4: autorização em todos os endpoints de jobs

**Arquivos:** modificar `worker/api.py`, `worker/tests/test_api_input.py`; criar `worker/tests/test_api_authz.py`.

**Consumidas:** `current_user`, `require_admin`, `check_csrf` (Tarefa 3); `get_job_for`, `list_jobs_for`, `create_job` (Tarefa 2).
**Produzidas:** as rotas de jobs com `user: dict = Depends(current_user)`; `POST /jobs` com `Depends(require_admin)` + `check_csrf`; `POST /jobs/async` com `check_csrf`, gravando `owner_id=user["user_id"]` e `tenant_id=user["tenant_id"]`; `GET /jobs/{task_id}` sem `AsyncResult` (import removido); helper `_job_or_404(task_id, user)`.

- [ ] **Passo 1: testes que falham** em `test_api_authz.py`:
  - `test_toda_rota_de_jobs_exige_usuario` (percorre `app.routes`; toda rota que começa com `/jobs` e `/auth/logout` tem `current_user` ou `require_admin` em `route.dependant.dependencies`);
  - `test_post_exige_csrf` (toda rota POST tem `check_csrf`);
  - `test_membro_recebe_404_nos_5_recursos_de_job_alheio` (status, mapa, memorial, resultado, input → `404 "Job não encontrado"`, igual ao inexistente);
  - `test_membro_nao_acessa_legado` e `test_admin_acessa_legado_e_job_de_membro`;
  - `test_listagem_passa_o_usuario_para_list_jobs_for`;
  - `test_job_fora_do_banco_404_sem_consultar_celery` (monkeypatch de `AsyncResult`, se ainda existir, que falha se chamado; e `not hasattr(api, "AsyncResult")`);
  - `test_async_grava_dono_e_tenant`;
  - `test_sync_so_admin`.
  - Em `test_api_input.py`: a fixture `jobs` passa a monkeypatchar `get_job_for` e as chamadas usam um usuário; os testes de caminho continuam iguais.
- [ ] **Passo 2:** rodar `-k "test_api_authz or test_api_input"` → FAIL.
- [ ] **Passo 3:** implementar.
- [ ] **Passo 4:** rodar a suíte pytest inteira → PASS (76 anteriores ajustados + novos).

### Tarefa 5: CLI de usuários

**Arquivos:** criar `worker/scripts/users.py`, `worker/tests/test_users_cli.py`.

**Produzidas:** `main(argv: list[str], stdin=sys.stdin) -> int`, com os comandos:
- `create --email E --role admin|member [--password-stdin]` — exit 0; 3 se o e-mail já existe;
- `reset-password --email E [--password-stdin]` — 0; 4 se não existe;
- `disable --email E` — 0; 4 se não existe.

A senha vem de `getpass.getpass` (duas vezes, precisam coincidir) ou da primeira linha do stdin; se tiver menos de 12 caracteres, exit 2. Nunca imprime a senha. Execução: `python3 scripts/users.py ...`.

- [ ] **Passo 1: testes que falham:**
  - `test_create_grava_hash_e_nunca_a_senha`;
  - `test_create_email_existente_exit_3`;
  - `test_senha_curta_exit_2`;
  - `test_reset_troca_hash_e_apaga_sessoes`;
  - `test_disable`;
  - `test_nao_aceita_senha_como_argumento` (`--password` → erro do argparse).
- [ ] **Passo 2:** rodar → FAIL. **Passo 3:** implementar. **Passo 4:** rodar → PASS.

### Tarefa 6: cliente HTTP com sessão

**Arquivos:** modificar `worker/web/js/api.js`, `tests-web/unit/api.test.mjs`.

**Produzidas:**
- `createApi(fetchImpl, { onUnauthorized } = {})`;
- todo POST envia `X-GeoLume-CSRF: 1`;
- `login(email, password)` → `POST /auth/login`, JSON;
- `logout()` → `POST /auth/logout`;
- `me()` → `GET /auth/me`;
- `ApiError` 401 chama `onUnauthorized()` antes de lançar, exceto em `login`, onde 401 é credencial errada.

- [ ] **Passo 1: testes que falham:**
  - `enqueue envia o header CSRF`;
  - `login envia JSON e o header`;
  - `401 chama onUnauthorized uma vez`;
  - `401 no login não chama onUnauthorized`;
  - `me devolve email e role`.
- [ ] **Passo 2:** `npm run test:unit` → FAIL. **Passo 3:** implementar. **Passo 4:** → PASS (57 + novos).

### Tarefa 7: tela de login, sair e limpeza de estado

**Arquivos:** modificar `worker/web/index.html`, `worker/web/js/app.js`, `worker/web/js/render.js`, `worker/web/styles.css`.

**Produzidas (test ids):**
- seção `login` com `login-email`, `login-password`, `login-submit`, `login-message` (`aria-live="polite"`);
- área de operação `app` (`hidden` até autenticar);
- no cabeçalho, `session-user` e `logout`;
- `renderSession(el, user | null)` em `render.js`;
- em `app.js`: `start(user)` (inicia os pollers) e `signOut()`, que para os pollers, limpa `shapes`, `inputs`, `metrics`, `job`, `selectedTaskId`, `jobsKey`, `file`, `preview`, o corpo do histórico, o detalhe e o mapa, e mostra o login.

`onUnauthorized` chama `signOut()` com a mensagem "Sua sessão expirou. Entre novamente."

- [ ] **Passo 1:** escrever os E2E da Tarefa 8, com o `auth.spec.mjs` vermelho.
- [ ] **Passo 2:** implementar. **Passo 3:** executar a Tarefa 8, Passo 4.

### Tarefa 8: E2E autenticado

**Arquivos:** criar `tests-web/global-setup.mjs`, `tests-web/e2e/auth.spec.mjs`; modificar `tests-web/playwright.config.mjs`, `tests-web/e2e/operacao.spec.mjs`, `tests-web/e2e/mapa.spec.mjs`, `.gitignore` (+ `tests-web/.auth/`).

**Produzidas:**
- O `global-setup` gera senhas com `crypto.randomBytes(24).toString("base64url")` para `e2e-admin@geolume.test` (admin), `e2e-a@geolume.test` e `e2e-b@geolume.test` (member).
- Roda `docker compose exec -T api python3 scripts/users.py create ... --password-stdin` com `spawn` na raiz do repo e a senha no stdin; se o exit for 3, usa `reset-password`.
- Faz login pelo `request` do Playwright e grava `tests-web/.auth/{admin,a,b}.json` (storageState) e `tests-web/.auth/users.json` (e-mails e senhas, para os testes de UI).
- Config: `globalSetup`, `use.storageState: ".auth/a.json"` por padrão.

- [ ] **Passo 1: testes em `auth.spec.mjs`** (sem storageState, 1440 e 375):
  - `sem login mostra só o formulário e a API responde 401` (os 6 endpoints via `request` anônimo);
  - `senha errada mostra erro genérico`;
  - `login e logout`, e após o logout os endpoints voltam a responder 401;
  - `A não vê job de B e a URL de B responde 404` nos 5 recursos;
  - `admin vê jobs legados; membro não`;
  - `sessão revogada no meio do uso volta ao login e limpa mapa e histórico` (logout por outro contexto com o mesmo cookie; depois login como B na mesma aba e nada de A aparece);
  - `cookie HttpOnly, Secure, SameSite=Lax`.
- [ ] **Passo 2:** rodar `npm run test:e2e` → os specs antigos falham com 401 e o `auth.spec` falha (ainda não existe UI de login).
- [ ] **Passo 3:** ajustar os specs antigos para usar o storageState, sem remover nenhum caso. Nos que usam `request`, conferir que o cookie é enviado (Foco da revisão 2).
- [ ] **Passo 4:** `npm run test:e2e > out.txt; echo exit=$?` → PASS em todos (38 + novos).

### Tarefa 9: implantação e verificação

**Arquivos:** modificar `worker/README.md` (login, CLI, regras de acesso, variáveis de sessão).

- [ ] **Passo 1:** `docker compose restart api celery`. Conferir nos logs que não há erro e, com `\dt`, que existem `tenants`, `users`, `sessions` e as colunas novas em `jobs`. Conferir `select count(*) from jobs where tenant_id='demo' and owner_id is null` = 144 + jobs criados desde então. O volume não pode ser recriado (a data de criação continua igual).
- [ ] **Passo 2:** Rodar um script Node lendo `tests-web/.auth/admin.json`: os legados via `/jobs/{id}` e `/input` com o admin devem dar 200; com `a.json`, 404; sem cookie, 401.
- [ ] **Passo 3:** Rodar pytest, JS unitário e Playwright, todos com exit code capturado.
- [ ] **Passo 4:** Chrome DevTools, desktop e 375 px: console sem erros; atributos do cookie; 401 → tela de login; nenhuma senha na aba Rede fora do POST de login.
- [ ] **Passo 5:** Codex somente leitura. Corrigir só os problemas confirmados, com TDD, e rodar tudo de novo.
- [ ] **Passo 6:** Relatório com as saídas reais dos testes. O admin da demonstração é criado pelo usuário: `docker compose exec api python3 scripts/users.py create --email <seu e-mail> --role admin`, com a senha pedida pelo terminal.

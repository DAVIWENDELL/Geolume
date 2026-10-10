# Sign-off do backend MVP — 2026-10-10

Relatório de verificação, sem alteração de código. Commit verificado: `c52b5b2d0d42882c4307fca2d6dc9c9d00db6a49`
(branch `feat/backend-layout-profissional`, 15 commits à frente do remoto, sem push). Tudo abaixo foi executado nesse
commit em 2026-10-10, exceto onde está escrito outra data.

Escopo: o MVP demonstrável do `PRD.md` (GeoJSON fictício → QGIS headless → `mapa.pdf`, `memorial.pdf` preliminar e
`resultado.json`). Não cobre produção, cobrança, multi-tenancy de produção nem dados de cliente.

## Execuções

| Execução | Resultado |
|---|---|
| Suíte do worker (Docker, QGIS 3.44) | 1645 passed, 2 skipped (os 2 módulos de integração, sem `GEOLUME_DB_INTEGRATION`) |
| Suíte do worker sem rede (`docker run --network none`) | 1645 passed, 2 skipped |
| Integração PostgreSQL real em schema `itest_*` + Redis DB 15 | 68 passed, 0 skipped |
| Frontend unitário (`node:test`) | 242/242 |
| E2E Playwright contra a API real (Chrome desktop e mobile) | 224/224 (`playwright test --list`: 112 `chrome-desktop` + 112 `chrome-mobile`). Eram 222 até o commit `a45d840` (lacuna A), que acrescentou o teste do polígono submilimétrico nos 2 navegadores |
| `diagnostico_jobs.py` (somente leitura, ambiente real) | 0 jobs presos, 0 candidatos |
| `diagnostico_fila.py` (somente leitura, ambiente real) | 0 jobs `queued` com `task_id`; broker nem consultado |
| `retencao.py` (dry-run, ambiente real) | 2319 jobs, todos `manter`; 0 candidatos |
| `recuperar_job.py` sem `--apply` | só diagnóstico (`somente_leitura: true`) |
| Integridade dos `completed` (leitura) | 2061 `completed`: 2054 íntegros, 7 inválidos (os históricos) |

## Itens verificados

| # | Item | Evidência | Estado |
|---|---|---|---|
| 1 | GeoJSON → validação → QGIS → `mapa.pdf` → `memorial.pdf` → `resultado.json` | `test_input_loader`, `test_processing`, `test_job`, `test_layout_*`, `test_memorial`; E2E gera e baixa os 3 artefatos; 60 jobs `completed` criados pelo E2E hoje | OK |
| 2 | Integridade dos 3 artefatos antes do `completed` | `integridade.conferir_artefatos` antes de `db.concluir_job` (`test_integridade` 245, `test_celery_app`, `test_job_duravel`); os 60 `completed` novos de hoje: 60 íntegros | OK |
| 3 | GeoJSON de job `failed` mantido (retenção 30 dias) | `test_celery_app`, `test_recuperacao_integracao`; E2E de hoje: 8 de 8 `failed` novos com o GeoJSON presente; `retencao.py` com `failed` = 30 dias | OK |
| 4 | `input_path` vinculado ao próprio job | `_safe_input_path(raw, job_id)`; `test_api_input` (43) e integração com `input_path` cruzado (outro tenant, mesmo `user_id` em outro tenant, outro dono no mesmo tenant) | OK |
| 5 | Isolamento por tenant no PostgreSQL real | `test_recuperacao_integracao` (schema `itest_*`, SQL real de `JOB_ACCESS`): 404 nos 6 recursos para colega, outro tenant e administrador de outro tenant; listagem só do próprio tenant | OK |
| 6 | Recuperação manual só com confirmação | `recuperar_job.py`: diagnóstico por padrão; escrita só com `--apply --job-id X --confirm-job-id X`; corrida de aplicações testada no Postgres real (`test_recuperar_job` 25, `test_recuperacao` 35). Nunca executado com `--apply` em job real | OK |
| 7 | Diagnóstico de fila somente leitura | `diagnostico_fila.py` (`LRANGE`/`HVALS`/`GET`, sessão `readonly`); `test_diagnostico_fila` 42, Redis real só na DB 15; execução de hoje: DB 1 igual (3 chaves) | OK |
| 8 | Retenção somente dry-run | `retencao.py` não tem modo de aplicar; `test_retencao` 57 (inclui disco bloqueado para escrita); execução de hoje sem mudança em banco ou `saida/` | OK |
| 9 | Execução sem rede | suíte completa em `--network none`: 1645 passed; `mapa.pdf` sem raster nem acesso à rede | OK |
| 10 | E2E desktop/mobile | 224/224, 112 por navegador (inclui o polígono submilimétrico com a mensagem pública) | OK |
| 11 | Frontend unitário | 242/242 | OK |
| 12 | Concorrência de conclusão | UPDATEs condicionais (`iniciar_job`, `falhar_job`, `concluir_job`, `falhar_enfileiramento`, `marcar_job_expirado`); corridas recuperação × conclusão/início/falha, API × Celery e 8 processos simultâneos testadas no Postgres real (`test_recuperacao_integracao`, `test_db_integration`) | OK |
| 13 | Celery parado e retomado | Fase C do QA de **2026-10-09**, **não repetida nesta auditoria** (exigiria parar o Celery): Celery parado, job enviado ficou `queued` com a mensagem na fila (`fila_confirmada`); Celery de volta e o job `completed` em ~3 s com artefatos íntegros | OK em 2026-10-09; não reexecutado em 2026-10-10 |
| 14 | Golden do memorial e do `resultado.json` | `test_contrato_artefatos` (47): texto do memorial e JSON iguais às referências, contrato de 10 chaves e tipos, prancha sem efeito no memorial e no JSON; 10 mutações detectadas | OK |
| 15 | Downloads, symlink, hard link e caminhos | `_open_validated` (`O_NOFOLLOW`, caminho real do descritor, `st_nlink == 1`), `_safe_job_file`, `_safe_input_path`; `test_api_input`, `test_api_authz`, `test_api_prancha` (troca por link entre validar e abrir, hard link, pasta trocada, traversal, caminho fora) | OK |

## Estado do ambiente real (somente leitura)

- Antes do E2E: 2319 jobs (2061 `completed`, 258 `failed`), 8938 arquivos em `saida/`, Redis DB 0 = 348, DB 1 = 3, DB 15 = 0.
- Depois do E2E: 2387 jobs (2121 `completed`, 266 `failed`), 9196 arquivos, DB 0 = 416, DB 1 = 3, DB 15 = 0.
- Nenhum job ou arquivo existente alterado ou removido (comparação por linha do banco e sha256 de cada arquivo), incluindo
  os 7 `completed` históricos corrompidos. Os 68 jobs novos são fictícios, criados pelo E2E (usuários de teste): 60 `completed`, 6 `failed` com `geometria_invalida`, 2 `failed` com
  `poligono_muito_pequeno`. As 68 chaves novas na DB 0 são resultados do Celery desses jobs e expiram sozinhas (TTL do backend de resultados).
- 0 jobs `queued`/`started`. Nenhuma recuperação, retenção ou limpeza executada.

## Limitações aceitas

- Os 7 jobs `completed` de teste de 2026-10-01 (17:49–17:50 UTC) com artefatos zerados ficam fora da demonstração.
  Não foram corrigidos de propósito; a conferência de integridade vale para jobs novos.
- Polígono que, depois da reprojeção para UTM e do arredondamento de E/N a três casas, fica com menos de 3 vértices é
  recusado com `poligono_muito_pequeno` (antes virava `artefato_invalido` com mensagem genérica).
- `acks_late` desligado: parar o Celery durante um processamento perde a mensagem e o job fica `started` até expirar
  (30 min) e ser recuperado à mão. Reiniciar o Celery só sem job `started`.
- A API responde 503 quando o enfileiramento falha mesmo que o Celery já tenha vencido a corrida (pendência registrada
  em `TASKS.md`).
- Memorial é preliminar, não documento legal.

## Fora do sign-off

Pendentes em `TASKS.md`, sem impacto no MVP demonstrável: destino dos 7 históricos, exclusão real da retenção,
`task_nao_localizada`, arquivos órfãos, rotina periódica, separação de dados de demonstração e de cliente,
infraestrutura de produção.

## Decisão

Nenhuma execução desta auditoria falhou, e os 15 itens têm evidência. Isso não significa ambiente sem defeitos:

- 14 itens foram verificados em 2026-10-10; o item 13 (Celery parado e retomado) se apoia na Fase C de 2026-10-09 e não
  foi repetido nesta auditoria;
- os 7 jobs `completed` históricos continuam com artefatos inválidos (2054 de 2061 `completed` íntegros) e foram
  mantidos sem alteração, fora da demonstração;
- as limitações acima são aceitas, não resolvidas.

A decisão de sign-off (marcar "QA final do backend MVP" em `TASKS.md`) fica com o responsável pelo projeto e a revisão do
Codex.

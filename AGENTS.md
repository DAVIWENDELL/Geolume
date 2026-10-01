# AGENTS.md — GeoLume

## Objetivo

GeoLume é uma plataforma web SaaS de geoprocessamento automatizado. O fluxo atual recebe um GeoJSON com um polígono, processa a geometria com PyQGIS em modo headless e entrega `mapa.pdf`, `memorial.pdf` preliminar e `resultado.json`.

O navegador é a experiência do cliente. QGIS é um motor interno e não deve ser exposto como interface de desktop.

## Fonte de verdade

1. Código e testes executáveis.
2. `worker/README.md` e este arquivo.
3. `ARCHITECTURE.md`, `TASKS.md`, `PROJECT_MEMORY.md` e planos/especificações versionados em `docs/`.
4. Memória compartilhada do AI Brain, usada como contexto histórico; confirme qualquer afirmação no repositório antes de alterar o código.

Se documentação, memória e código divergirem, não invente uma reconciliação: registre a divergência e trate código/testes como estado atual.

## Stack confirmada

- Python 3.12+ no worker.
- FastAPI para API e arquivos estáticos.
- PyQGIS headless em `qgis/qgis:3.44.14-noble`.
- Celery com `--pool=solo` para jobs assíncronos.
- Redis como broker/backend.
- PostgreSQL/PostGIS para usuários, sessões e jobs.
- HTML/CSS/JavaScript puro no frontend.
- Leaflet 1.9.4 copiado em `worker/web/vendor/leaflet/`.
- Playwright e `node:test` em `tests-web/`.
- Docker Compose como ambiente de verdade para PyQGIS e integração.

## Regras obrigatórias

- Não use `docker compose down -v` em manutenção normal; isso pode apagar o volume PostgreSQL.
- Não encerre e reabra uma sessão QGIS no mesmo processo Celery; o worker mantém uma sessão por processo para evitar SIGSEGV.
- Não altere contrato de API, formatos de PDF ou autenticação sem atualizar testes e documentação.
- Não trate o memorial como documento legal definitivo; ele é explicitamente preliminar.
- Não adicione autenticação, cobrança, multi-tenancy ou infraestrutura de produção sem plano e critérios de aceitação.
- Não coloque senhas, tokens, `.env.local` ou dados de clientes em commits.
- Não exponha caminhos internos, `owner_id`, `tenant_id`, stack traces ou exceções brutas nas respostas da API.
- Validação no navegador é conveniência; toda validação de upload, logo e prancha deve existir no servidor e ser repetida no worker quando necessário.
- Preserve jobs, fixtures e resultados existentes durante mudanças; não apague `saida/` para “limpar” o ambiente.

## Fluxo de trabalho

1. Consulte o AI Brain em modo somente leitura e inspecione o repositório.
2. Leia os testes relacionados antes de alterar o comportamento.
3. Faça a menor mudança vertical possível.
4. Para comportamento novo, escreva primeiro um teste focado e confirme RED.
5. Implemente o mínimo, confirme GREEN e rode a suíte aplicável.
6. Para mudanças em PyQGIS/PDF, execute dentro do Docker e inspecione o artefato real.
7. Para mudanças no navegador, rode testes unitários e E2E contra a API real.
8. Atualize `TASKS.md` e `PROJECT_MEMORY.md` somente com fatos verificados.

## Comandos principais (PowerShell)

```powershell
cd D:\Projetos\Geolume

docker compose build worker
docker compose run --rm worker
docker compose up -d api redis postgis celery
Invoke-RestMethod http://localhost:8000/health

cd tests-web
npm run test:unit
npm run test:e2e
```

O E2E exige Node 24, Chrome e o stack local em `http://localhost:8000`. A URL de teste deve continuar restrita a localhost.

## Sincronização automática de contexto

Ao concluir uma tarefa, o agente deve comparar as mudanças com `AGENTS.md`, `ARCHITECTURE.md`, `PRD.md`, `DESIGN.md`, `TASKS.md` e `PROJECT_MEMORY.md`.

- Se a tarefa mudar arquitetura, contrato de API, segurança, formato de entrada/saída, fluxo do usuário ou uma decisão importante, atualize os documentos relevantes no mesmo trabalho.
- Se for uma correção interna sem impacto no contexto, não crie alterações de documentação só para gerar atividade.
- Registre em `TASKS.md` somente tarefas reais e verificadas.
- Registre em `PROJECT_MEMORY.md` somente decisões e fatos duráveis; nunca senhas, tokens ou dados de clientes.
- O Claude Code consegue manter os arquivos do repositório, mas não deve ser considerado conectado ao AI Brain do Hermes. Depois de uma tarefa importante, a conversa principal do GeoLume no Hermes deve revisar o resumo e sincronizar apenas os fatos novos no AI Brain.

## Critério de conclusão

Uma alteração só está concluída quando o teste aplicável passa, o ambiente real exigido foi exercitado, os arquivos gerados foram verificados e a documentação de contexto foi atualizada quando o contrato ou uma decisão mudou.

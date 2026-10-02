# GeoLume

Plataforma web SaaS de geoprocessamento automatizado. O fluxo atual recebe um GeoJSON com um polígono, processa a geometria com PyQGIS em modo headless e entrega `mapa.pdf`, `memorial.pdf` preliminar e `resultado.json`.

## Stack

- Python 3.12+
- FastAPI
- PyQGIS headless (`qgis/qgis:3.44.14-noble`)
- Celery com Redis
- PostgreSQL/PostGIS
- HTML, CSS e JavaScript no frontend
- Leaflet 1.9.4
- Docker Compose
- Playwright e `node:test`

## Como executar

O ambiente de desenvolvimento validado usa Docker Compose:

```powershell
docker compose up -d api redis celery postgis
Invoke-RestMethod http://localhost:8000/health
```

Para executar o worker:

```powershell
docker compose build worker
docker compose run --rm worker
```

O ambiente local deve ser usado apenas em `localhost` durante os testes.

## Testes web

Com Node 24, Chrome e o stack local em execução:

```powershell
cd tests-web
npm run test:unit
npm run test:e2e
```

## Fluxo principal

1. O usuário envia um GeoJSON contendo um polígono.
2. A API valida e encaminha o processamento.
3. O worker executa o processamento geoespacial com PyQGIS.
4. Os artefatos gerados ficam disponíveis para consulta e download.

## Estrutura do projeto

- `worker/` — processamento geoespacial e aplicação web;
- `tests-web/` — testes unitários e E2E;
- `docs/` — documentação do projeto;
- `ARCHITECTURE.md` — decisões e visão arquitetural;
- `TASKS.md` — tarefas verificadas;
- `docker-compose.yml` — ambiente local.

## Observações

O memorial gerado atualmente é preliminar e não deve ser tratado como documento legal definitivo. O QGIS é um motor interno de processamento; a experiência do cliente acontece pelo navegador.

## Status

Projeto em desenvolvimento.

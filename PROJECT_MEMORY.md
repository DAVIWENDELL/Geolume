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

- Existem referências históricas a jobs que ficaram presos em `started` durante uma falha anterior; investigar antes de criar rotina de recuperação.
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

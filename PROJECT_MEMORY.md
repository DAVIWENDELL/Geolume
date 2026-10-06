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
- 2026-10-01 — Catálogo de camadas definido só no servidor (`geolume_worker/camadas.py`, servido por `GET /camadas` com sessão). Motivo: uma fonte única para tela e PDF. Camadas sem fonte licenciada (satélite, limites, hidrografia, rodovias) ficam desabilitadas e sem URL até haver provedor confirmado.
- 2026-10-01 — O `mapa.pdf` não contém camada raster nem acessa a rede; OSM é só pré-visualização. Verificado gerando PDFs em container `--network none`.
- 2026-10-01 — Alfa do preenchimento: `alfa8 = floor(alfa × 255 + 0,5)` (metade para cima) no Python e no JavaScript. O `round()` do Python foi rejeitado porque arredonda ao par e faria tela e PDF divergirem em 30 % e 70 %. Alfa inválido é recusado, nunca corrigido.
- 2026-10-01 — Jobs antigos sem `estilo`/`alfa_preenchimento` valem `padrao`/0.35, sem migração de banco.
- 2026-10-01 — O frontend não tem mais lista local de mapas-base nem fallback de catálogo: sem catálogo válido, `withBase` não troca o mapa-base e a legenda mostra "Mapa-base: nenhum". O polígono muda só por `mapView.setStyle` (prancha completa). Não reintroduzir lista fixa no navegador.
- 2026-10-05 — Quadro de coordenadas do `mapa.pdf`: o ponto de referência é o centroide calculado no plano UTM e levado a SIRGAS 2000 geográficas (EPSG:4674), para que GMS e E/N mostrem o mesmo ponto. GMS com segundos em centésimos arredondados metade para cima sobre inteiros (59,995" sobe o minuto; nunca 60"), hemisférios S/N e L/O, separador decimal ponto como o resto do PDF. A fonte é declarada só como "GeoJSON fornecido pelo usuário", nunca como dado oficial ou cadastral. `centroide_geo` existe só em `ParcelSummary`; `resultado.json` não mudou.
- 2026-10-06 — Marca de autoria no `mapa.pdf`: todo mapa traz o símbolo oficial local do GeoLume (`worker/web/assets/geolume-marca-transparente.png`, o mesmo arquivo da tela) e o texto "Gerado pelo GeoLume", no rodapé da coluna direita, alinhados à base do quadro de fontes. É identificação visual de procedência do sistema, não selo técnico, certificação, aprovação, CREA nem garantia de precisão; o PDF não deve ganhar textos desse tipo. Independe da logo enviada pelo cliente (canto superior direito). Alternativas rejeitadas: selo técnico ou marca inventada sem arquivo autorizado; cópia reduzida da logo (o arquivo oficial é embutido inteiro e o `mapa.pdf` passou de ~24 KB para ~234 KB, aceito no MVP). Coberto por `worker/tests/test_layout_autoria.py`; API, prancha, `memorial.pdf` e `resultado.json` não mudaram.

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

# GeoLume — Catálogo de camadas e estilos cartográficos — Design

Data: 2026-09-30 · Status: aprovado em conversa (opção (a) para a opacidade); spec aguardando revisão

## Objetivo

1. Um **catálogo único de camadas**, definido no servidor, que diz com honestidade o
   que existe: camadas disponíveis, camadas que dependem de fonte oficial e camadas
   planejadas. Nenhuma camada aparece como disponível sem fonte real.
2. **Estilos de polígono prontos** (Padrão, Técnico, Preto e branco) e a
   **opacidade do preenchimento** passam a fazer parte do estilo exportado: a
   pré-visualização e o `mapa.pdf` usam as mesmas cores, espessura e alfa.
3. Um painel "Camadas do mapa" no navegador, montado a partir do catálogo.

Fora deste slice: qualquer integração com IBGE, ANA, DNIT ou satélite; ruas no
PDF; trânsito, transporte público e rotas; editor livre de estilos; QGIS no
navegador; mudanças em Dockerfile, docker-compose ou no volume do PostgreSQL.

## Estado atual (verificado em 2026-09-30)

| Camada | Pré-visualização | `mapa.pdf` | Fonte |
|---|---|---|---|
| Polígono do imóvel | Leaflet (`map.js`) | QGIS (`layout.py`) | GeoJSON enviado pelo usuário |
| Mapa de ruas | `tile.openstreetmap.org` | não entra | OSM, sem SLA |
| Sem mapa-base | sim | o PDF é sempre assim | — |
| Satélite | desabilitado ("em breve") | não | nenhuma licença |

- PostGIS: só `jobs`, `users`, `sessions`, `tenants`. O schema `tiger.*` é da
  extensão `postgis_tiger_geocoder` (censo dos EUA) e está vazio (0 linhas em
  `edges`, `county`, `state`).
- Nenhum `.shp`, `.gpkg`, `.tif`, `.mbtiles` ou `.pmtiles` no projeto.
- Portanto **não há fonte real de ruas vetoriais, edificações, hidrografia ou limites**.

Divergências que este slice fecha:

1. A opacidade escolhida na tela não vai para o PDF (o PDF usa alfa fixo 90/255).
2. A legenda da tela fala do mapa-base; a interface não avisa que as ruas não
   saem no PDF.
3. A lista de mapas-base está fixa no JavaScript (`BASEMAPS` em `layers.js`).

## Contrato de camada

Definido em `worker/geolume_worker/camadas.py`, imutável, servido por
`GET /camadas`.

| Campo | Tipo | Regra |
|---|---|---|
| `id` | str | `[a-z0-9_]{1,32}`, único |
| `nome` | str | texto exibido |
| `tipo` | `"vetor"` \| `"raster"` \| `"nenhum"` | |
| `grupo` | `"base"` \| `"sobreposicao"` | mapa-base é escolha única; sobreposição liga/desliga |
| `situacao` | `"disponivel"` \| `"depende_fonte_oficial"` \| `"planejada"` | |
| `fonte` | objeto \| null | `{ nome, licenca, termos_url, atribuicao: { texto, url }, atualizacao }` |
| `url` | str \| null | só para raster disponível; `https://` obrigatório |
| `max_zoom` | int \| null | |
| `exporta_pdf` | bool | |
| `visivel` | bool | visibilidade padrão |
| `ordem` | int | ordem de desenho; maior fica por cima |
| `aviso` | str \| null | texto curto exibido ao lado (ex.: "Somente na pré-visualização") |

Invariantes (testadas):

- `situacao != "disponivel"` ⇒ `url` nulo, `exporta_pdf` falso, `visivel` falso.
- `url` não nulo ⇒ `situacao == "disponivel"`, `tipo == "raster"`, `fonte` com atribuição.
- `exporta_pdf` verdadeiro ⇒ `tipo == "vetor"`, `url` nulo, desenhada pelo QGIS **sem rede**
  (só `poligono` neste slice).
- `ordem` do polígono é maior que a de qualquer mapa-base.
- Atribuição é texto + URL, nunca HTML; o navegador monta o link por DOM/escape.

### Catálogo inicial

| id | nome | grupo | situação | exporta_pdf | ordem | aviso |
|---|---|---|---|---|---|---|
| `ruas_osm` | Mapa de ruas (OpenStreetMap) | base | disponivel | não | 0 | Somente na pré-visualização — não entra no PDF |
| `nenhum` | Sem mapa-base | base | disponivel | não (o PDF já não tem mapa-base) | 0 | — |
| `poligono` | Polígono do imóvel | sobreposicao | disponivel | sim | 100 | — |
| `limites_municipais` | Limites municipais | sobreposicao | depende_fonte_oficial | não | 50 | Fonte oficial não definida |
| `hidrografia` | Hidrografia | sobreposicao | depende_fonte_oficial | não | 40 | Fonte oficial não definida |
| `rodovias` | Rodovias | sobreposicao | depende_fonte_oficial | não | 45 | Fonte oficial não definida |
| `satelite` | Satélite | base | depende_fonte_oficial | não | 0 | Sem provedor licenciado |
| `topografia` | Curvas de nível / relevo | sobreposicao | planejada | não | 30 | Planejada |
| `edificacoes` | Edificações | sobreposicao | planejada | não | 60 | Planejada |

`ruas_osm.fonte`: OpenStreetMap, ODbL, `https://www.openstreetmap.org/copyright`,
atribuição "© Contribuidores do OpenStreetMap", atualização contínua.

`fonte` das camadas não disponíveis é `null`: nenhuma fonte foi escolhida ainda.

## Estilos de polígono

Também em `camadas.py`, com espessura em mm (PDF) e em px (Leaflet) na mesma definição.

| id | nome | contorno | preenchimento | espessura PDF | espessura tela |
|---|---|---|---|---|---|
| `padrao` | Padrão | `#C80000` | `#FFC800` | 0,6 mm | 3 px |
| `tecnico` | Técnico | `#1F2937` | `#9CA3AF` | 0,35 mm | 2 px |
| `pb` | Preto e branco | `#000000` | `#FFFFFF` | 0,5 mm | 2 px |

- Escolher um estilo preenche os seletores de cor com as cores do estilo; o usuário
  ainda pode mudar as cores. Ficam gravados `estilo` (define a espessura) e as cores
  efetivas (já existentes em `prancha`).
- "Urbano claro", "Ambiental", "Satélite com limites" e "Personalizado" ficam como
  planejados; não aparecem como opção selecionável.

## Opacidade do preenchimento (opção (a))

- Novo campo `alfa_preenchimento` na prancha: número em **[0, 1]**, padrão **0,35**.
- O contorno fica sempre com opacidade 1.
- Validação idêntica nas três camadas, **recusando** (não corrigindo) valores
  inválidos: não numérico, `NaN`, infinito, booleano, fora de [0, 1]. Código de erro
  `alfa_invalido`, mensagem fixa "Opacidade do preenchimento deve estar entre 0 e 1."
  - Navegador (`prancha.js`): o controle deslizante 0–100 % é convertido para 0–1;
    valor fora da faixa bloqueia o envio com mensagem no formulário. O atual
    `opacityFromPercent`, que corrige silenciosamente, deixa de ser usado no envio.
  - API (`api.py`): campo de formulário `alfa_preenchimento`, validado por `validar_prancha`.
  - Worker (`prancha.py`): `validar_prancha` roda de novo antes de gerar o PDF.
- Conversão única, `alfa8 = round(alfa * 255)`: o QGIS usa `alfa8` e o Leaflet usa
  `alfa8 / 255`, então tela e PDF têm exatamente o mesmo alfa. **Mudança conhecida:**
  o padrão passa de 90/255 (35,3 %) para 89/255 (34,9 %). Diferença visualmente
  imperceptível; os PDFs já gerados não mudam.
- O controle de opacidade passa a fazer parte da personalização da prancha (vai para
  o PDF). Ao abrir um job do histórico, a pré-visualização usa o alfa gravado naquele job.

## Compatibilidade

- `prancha` JSONB nulo, ou sem `estilo`/`alfa_preenchimento` ⇒ `estilo="padrao"`,
  `alfa_preenchimento=0.35`. Sem migração de banco.
- `Prancha().padrao` continua verdadeiro para o envio sem personalização; o multipart
  só inclui campos diferentes do padrão, como hoje.
- Autenticação, autorização, histórico, downloads, logo e memorial sem mudança.

## Componentes e fluxo

1. **`camadas.py`** (novo): `CAMADAS`, `ESTILOS`, `ALFA_PADRAO`, `validar_alfa`,
   `estilo_por_id`, `catalogo_publico()` (dicionários prontos para JSON). Sem QGIS.
2. **`prancha.py`**: `Prancha` ganha `estilo` e `alfa_preenchimento`; `validar_prancha`
   valida ambos.
3. **`layout.py`**: `_simbolo(prancha)` usa cores, `round(alfa*255)` e a espessura do
   estilo; a amostra da legenda usa o mesmo símbolo. Nenhuma camada de rede é adicionada
   ao projeto QGIS.
4. **`api.py`**: `GET /camadas` (exige sessão; resposta constante, `Cache-Control:
   private, max-age=300`); `POST` do job aceita `estilo` e `alfa_preenchimento`.
5. **Navegador**:
   - `layers.js`: deixa de ter `BASEMAPS` fixo; recebe o catálogo e expõe funções puras
     (`grupos`, `basemaps`, `camadaAtivavel`, `atribuicaoHtml` com escape).
   - `api.js`: `getCamadas()`.
   - `map.js`: mapa-base e polígono em panes com z-index derivado de `ordem`; estilo
     (cores, espessura, alfa) aplicado de uma vez por `setStyle`.
   - `render.js`: painel "Camadas do mapa" com os três grupos; itens não disponíveis
     aparecem desabilitados com o aviso; legenda da tela inclui o aviso das ruas.
   - `prancha.js`: estilos, `validateAlfa`, campos `estilo` e `alfa_preenchimento`.
   - `app.js`, `index.html`, `styles.css`: ligação, cartões de estilo, painel.

## Falhas

- **Catálogo não carrega** (`GET /camadas` falha): o painel mostra "Catálogo de
  camadas indisponível"; o mapa usa só o polígono, **sem mapa-base** e sem nenhuma
  requisição de tiles. O envio de jobs continua funcionando.
- **Tiles OSM falham** (`tileerror`): a legenda mostra "Mapa de ruas — indisponível";
  o polígono continua desenhado (comportamento atual, mantido e testado).
- **PDF**: nunca depende de rede; a falha da fonte externa não afeta o job.

## Riscos de licença e disponibilidade

- **OSM tiles**: política de uso proíbe uso pesado, pré-download e uso offline;
  atribuição obrigatória; sem SLA. Aceitável para pré-visualização de baixo volume.
  Produção exigirá servidor de tiles próprio ou provedor contratado (fora do slice).
  Não baixar tiles no servidor.
- **Satélite**: provedores comuns não permitem esse uso sem contrato. Continua desabilitado.
- **IBGE / ANA / DNIT**: dados públicos, mas termos, frequência de atualização,
  formato e estabilidade ainda não foram verificados. Slice próprio de pesquisa antes
  de qualquer integração.

## Testes (TDD — RED antes de cada mudança)

- **Contrato** (`test_camadas.py`): ids únicos e válidos; invariantes acima; catálogo
  público sem campos inesperados; atribuição sem `<`/`>`.
- **Alfa** (pytest + unit JS, mesma tabela de casos): 0, 0,35, 1 aceitos; −0,01,
  1,01, `"abc"`, `""` com campo enviado, `NaN`, `Infinity`, `true` recusados; ausente ⇒ 0,35.
- **Estilo**: id desconhecido recusado na API e no worker; jobs antigos ⇒ padrão.
- **PDF** (`test_layout_prancha.py`): símbolo com a cor, espessura e alfa de cada estilo;
  alfa 0 e 1 geram PDF válido; nenhuma camada não vetorial/memória no projeto QGIS.
- **API** (`test_api_prancha.py`, `test_api_authz.py`): `/camadas` exige sessão;
  POST com alfa/estilo inválido ⇒ 422 com mensagem fixa; valor gravado e devolvido no job.
- **Navegador** (unit): visibilidade (camada não disponível não ativa), ordem (pane do
  polígono acima do mapa-base), estilo aplicado igual ao do catálogo, escape da atribuição.
- **E2E** (Playwright, desktop e mobile): painel com os três grupos; ruas com aviso de
  pré-visualização; tiles bloqueados ⇒ aviso e polígono visível; catálogo com 500 ⇒
  mapa sem tiles e sem requisições a `tile.openstreetmap.org`; estilo e alfa escolhidos
  aparecem no job e no PDF (texto da legenda com pdftotext, cor da amostra por
  inspeção do PDF); regressão de login, histórico e downloads.
- **Chrome DevTools**: rede (nenhum tile com catálogo falho), console limpo,
  acessibilidade do painel (rótulos, foco, itens desabilitados anunciados).
- **Revisão**: Codex, somente leitura; só corrigir achados confirmados com teste de regressão.

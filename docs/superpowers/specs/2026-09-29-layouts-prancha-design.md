# GeoLume — Layouts prontos de prancha e memorial paginado — Design

Data: 2026-09-29 · Status: aprovado em conversa; spec aguardando revisão

## Objetivo

1. O usuário escolhe, por cartões visuais, um de três layouts prontos para o
   `mapa.pdf`: **Padrão**, **Legenda lateral**, **Legenda inferior**. A
   pré-visualização e o PDF refletem a escolha.
2. Nenhum texto do `mapa.pdf` sai da página, é cortado ou fica sobreposto,
   inclusive com muitos vértices e propriedades longas.
3. O `memorial.pdf` passa a ter layout dinâmico e paginado, com **todos** os
   vértices, legível e sem sobreposição.

Fora deste slice: quadro de localização (precisa de fonte de dados offline,
escala e atualização definidas; exige mudar a imagem), editor livre de prancha.

## Estado atual (medido em 2026-09-29, container `api`, fixture `gleba_rural_exemplo`)

`mapa.pdf` — A4 paisagem 297 × 210 mm, caixas em mm (x0, y0 → x1, y1):

| Item | Caixa |
|---|---|
| Título / responsável | 10, 8 → ≤ 232, ≤ 24 (já ajustados e testados) |
| Logo | 237, 5 → 287, 22 |
| Mapa | 10, 25 → 210, 150 |
| Norte | 240, 25 → 260, 50 |
| Propriedades (≤ 6 linhas, 8 pt) | 218, 68 → ~271, 112 |
| Resumo (EPSG, área, perímetro, vértices) | 218, 120 → 280, 137 |
| Escala | 218, 155 → 287, 168 |
| Legenda lateral | 218, 173 → 253, 186 |
| Tabela de vértices (7 pt) | 10, 153 → 112, 160 + ~2,9 mm/linha |

Defeitos confirmados:

- **Mapa, tabela:** 14 vértices chegam a y = 206,8 (passa da margem de 5 mm);
  16 → 212,5; 30 → 252,9 (fora da página). O loader aceita até 5000.
- **Mapa, propriedades:** valores do GeoJSON não têm limite de tamanho e não são
  quebrados nem truncados; um valor longo passa de x = 292.
- **Memorial:** página única com posições fixas. A tabela começa em y = 98 mm e
  "Descrição perimetral" fica fixa em y = 145 mm. Com 14 vértices a última linha
  encosta no título; com 16 sobrepõe; com 30 tabela e descrição se misturam.
  `pdfinfo` diz 1 página mesmo assim. A identificação e a descrição quebram por
  contagem de caracteres (`textwrap`, fonte proporcional): texto longo pode
  passar da margem.

## Decisões

| # | Decisão |
|---|---|
| D1 | **Contrato A:** o campo `legenda` continua sendo a fonte de verdade; valores aceitos `nenhuma` (Padrão), `lateral`, `inferior`. Sem migração: jobs com `prancha` nula ou sem `legenda` usam o Padrão. |
| D2 | Validação em três camadas com a mesma lista: navegador (`prancha.js`), API (`validar_prancha` antes de gravar logo, entrada ou job) e worker (`run_job` revalida). Mensagem fixa, sem ecoar o valor: `"Legenda deve ser 'nenhuma', 'lateral' ou 'inferior'."` (API/worker) e `"Layout da prancha inválido."` (navegador). |
| D3 | Zonas do mapa viram constantes nomeadas em `layout.py` (margem 5 mm; mapa; coluna direita x 218–287; faixa inferior x 117–210, y 153–205; tabela x 10–112, y 153–205). Posições do layout Padrão não mudam. |
| D4 | **Legenda inferior:** "Legenda" + amostra + "Limite do imóvel" em x = 120, y = 155–170 (na faixa inferior, abaixo do mapa, à direita da tabela). Nunca invade a tabela, cuja largura é fixa (≤ 112 mm, texto monoespaçado de largura constante). |
| D5 | **Tabela do mapa limitada à área segura:** linhas que cabem entre y = 160 e y = 205 menos a linha de aviso. Quando não cabem todas: `"Exibidos K de M vértices. Demais vértices no memorial descritivo."` logo abaixo da última linha, dentro da margem. Fonte continua 7 pt. A contagem é feita medindo o rótulo real (`adjustSizeToText`), não por estimativa. |
| D6 | **Propriedades no mapa:** cada linha cabe em uma linha da coluna direita (≤ 69 mm, 8 pt). Valor longo é cortado no ponto que cabe, com "…". O valor completo continua no memorial e no `resultado.json`. |
| D7 | **Memorial paginado:** um paginador em `memorial.py` empilha blocos com altura medida (`adjustSizeToText`) numa área útil A4 retrato de 18–192 mm × 15–260 mm; o rodapé fica em y = 265 em **todas** as páginas. Quando o próximo bloco não cabe, adiciona página (`pageCollection().addPage`). Fontes não mudam (título 16, seções 12, texto 10, tabela 8). |
| D8 | **Tabela do memorial:** fatiada por página; cada fatia repete a linha de cabeçalho (`Vértice  Este (m) …`). A primeira página tem "Quadro de vértices"; as seguintes, "Quadro de vértices (continuação)". Título de seção nunca fica sozinho no fim de uma página (vai junto com pelo menos uma linha). |
| D9 | **Descrição perimetral** começa em `max(145, fim_da_tabela + 8)` mm, ou seja, casos pequenos (≤ ~13 vértices) ficam na posição de hoje. Seus parágrafos quebram por **largura medida** (métrica da fonte), não por número de caracteres, e continuam na página seguinte se preciso. Mesma regra para a identificação do imóvel. Palavras maiores que a largura são quebradas por caractere. |
| D10 | Conteúdo textual do memorial não muda (mesmas frases, mesmos campos). A prancha continua sem afetar o memorial. |
| D11 | UI: o `<select>` vira um `fieldset` "Layout da prancha" com 3 rádios nativos (`name="prancha-legenda"`), cada um num cartão com miniatura A4 (divs/CSS: mapa, coluna direita, tabela, legenda na posição do layout), nome e uma linha de descrição. Teclado: setas entre cartões, foco visível. A pré-visualização ganha a mesma miniatura, marcando o layout ativo, e a legenda do cabeçalho aparece para `lateral` e `inferior`. Job do histórico mostra o layout dele; prancha nula → Padrão. |

## Componentes

- `geolume_worker/prancha.py` — `LEGENDAS = ("nenhuma", "lateral", "inferior")`; mensagem.
- `geolume_worker/medida.py` (novo) — medição por rótulo QGIS real (DejaVu Sans,
  `texto_literal`): `medir`, `quebrar_por_largura`, `cortar_para_caber`,
  `linhas_que_cabem`. Usado pelo mapa e pelo memorial.
- `geolume_worker/layout.py` — constantes de zona; `_legenda(layout, prancha, x, y)`
  reaproveitada pelos dois layouts com legenda; tabela com limite e aviso (D5);
  propriedades cortadas com `cortar_para_caber` (D6).
- `geolume_worker/memorial.py` — `montar_memorial` + `_Paginador` (D7–D9).
- `web/index.html`, `web/css/*`, `web/js/prancha.js` (`LAYOUTS` com rótulo e
  descrição; validação), `web/js/render.js` (miniatura), `web/js/app.js`
  (leitura do rádio marcado, restaurar padrão, histórico).
- README (layouts e memorial paginado).

Sem mudança: `api.py` (usa `validar_prancha`), `job.py`, `db.py`, `celery_app.py`,
Dockerfile, `docker-compose.yml`, Redis, PostGIS, volume do PostgreSQL.

## Fluxo de dados

Cartão marcado → `validatePrancha` → multipart só com o que difere do padrão
(`legenda` omitida quando `nenhuma`) → `POST /jobs/async` → `validar_prancha`
(422 antes de gravar qualquer coisa) → `jobs.prancha` JSONB → Celery →
`run_job` revalida → `montar_mapa` escolhe a posição da legenda →
`GET /jobs/...` devolve `prancha` revalidada → UI desenha a miniatura do job.

## Erros e segurança

- Valor desconhecido de `legenda` (inclusive `" lateral"`, `"LATERAL"`, número,
  lista, texto de 10 kB): 422 com a mensagem fixa; nenhum arquivo em `inputs/`
  ou `logos/`, nenhum job no banco, nada na fila.
- Registro antigo com `legenda` inválida no banco: `_prancha_publica` devolve nulo
  (já é assim) e o worker recusa com `InvalidInputError`.
- Nenhum caminho interno em mensagens, JSON ou PDF. Textos seguem por
  `texto_literal` (sem avaliação `[% %]`). UI só com `textContent`.

## Testes (TDD: cada teste roda vermelho antes da implementação)

Worker (pytest no container):

- `validar_prancha`: aceita os três; recusa `topo`, `" lateral"`, `"LATERAL"`, `1`,
  `[]`, texto longo — mensagem não contém o valor.
- **Sobreposição e margens do mapa** — matriz: 3 layouts × {sem logo, logo} ×
  {nomes curtos, projeto e responsável com 100 caracteres} × {cores padrão,
  customizadas} × {6, 14, 16, 30 vértices} × {propriedades normais, 500
  caracteres}. No layout: toda caixa dentro de 5–292 × 5–205; nenhum par de
  grupos (cabeçalho, logo, mapa, norte, propriedades, resumo, escala, tabela,
  aviso, legenda) se intersecta.
- **PDF do mapa:** `pdfinfo` = 1 página; `pdftotext -bbox`: toda palavra dentro
  da página menos a margem e nenhuma interseção relevante (> 0,5 pt²) entre
  palavras de linhas diferentes; "Limite do imóvel" só nos layouts com legenda;
  norte (imagem SVG), escala e "Tabela de vértices" presentes; com 14, 16 e 30
  vértices o aviso aparece com K e M corretos, e com 6 não aparece.
- Legenda inferior: amostra com as cores da prancha no raster, na faixa esperada.
- Padrão inalterado: `pdftotext -layout` igual ao de hoje para 6 vértices sem prancha.
- **Memorial** com 6, 14, 16, 30 vértices, propriedades de 500 caracteres
  (nome, município, proprietário) e um caso de 300 vértices:
  todos os `V1…VM` no texto extraído; "Descrição perimetral" depois da última
  linha da tabela (página maior, ou mesma página e y maior); cabeçalho repetido
  em cada página com tabela; nenhuma palavra fora das margens; nenhuma
  sobreposição; rodapé em cada página; `pdfinfo` abre e conta páginas ≥ 1;
  tamanho da fonte da tabela e do texto inalterado.
- `quebrar_por_largura`: nenhuma linha passa da largura; palavra gigante quebrada;
  texto curto intacto.
- `run_job` com job antigo (`prancha=None`) → layout Padrão; com `inferior` → legenda.

API: `legenda=inferior` → 202, gravado e devolvido; valor adulterado → 422 com
disco, banco e fila intactos; dono/tenant preservados; jobs sem prancha → nulo.

Navegador:

- Unit (`node:test`): `validatePrancha` com os três valores e adulterados;
  `pranchaFields` omite `nenhuma`.
- Playwright, 1280 × 800 e 390 × 844: três cartões, rádio por teclado; miniatura e
  legenda do cabeçalho mudam; envio leva `legenda` correta; histórico mostra o
  layout do job; job antigo → Padrão; rádio adulterado no DOM bloqueia o envio
  com mensagem; sem rolagem horizontal no mobile; "Restaurar padrão" volta ao Padrão.
- Chrome DevTools: screenshots desktop/mobile, console sem erros, árvore de
  acessibilidade dos cartões (grupo com nome, rádios com rótulo).
- Ponta a ponta real (Celery + QGIS): um job por layout com 30 vértices, logo,
  nomes longos e cores; baixar `mapa.pdf` (1 página) e `memorial.pdf` (todas as
  linhas) e passar pelos mesmos verificadores.

Revisão: Codex somente leitura ao final; só achados confirmados são corrigidos,
cada um com teste de regressão vermelho antes.

## Critérios de aceite

- `mapa.pdf` sempre com 1 página, nada fora da margem nem sobreposto, nos três layouts.
- `memorial.pdf` com todos os vértices, "Descrição perimetral" depois da tabela,
  cabeçalho repetido, nada fora das margens, pode ter várias páginas.
- Jobs antigos continuam com layout Padrão e abrindo normalmente.
- Valor de legenda desconhecido recusado no navegador, na API e no worker.
- Suítes existentes (pytest, unit JS, Playwright) continuam verdes.

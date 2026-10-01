# Layouts prontos de prancha e memorial paginado — Plano de implementação

> **Para quem executa:** usar superpowers:executing-plans (ou subagent-driven-development)
> tarefa a tarefa. Passos com `- [ ]`. O projeto não é repositório git: sem passos de commit.

**Objetivo:** três layouts de prancha escolhidos por cartões (Padrão, Legenda lateral,
Legenda inferior); `mapa.pdf` sempre em 1 página sem nada cortado ou sobreposto;
`memorial.pdf` paginado com todos os vértices.

**Arquitetura:** medição de texto por rótulo QGIS real num módulo novo
(`geolume_worker/medida.py`), usada por `layout.py` (tabela limitada, propriedades
cortadas, legenda inferior) e por `memorial.py` (paginador). O contrato continua
sendo o campo `legenda`, agora com `inferior`. A UI troca o `<select>` por rádios
em cartões com miniatura A4.

**Stack:** PyQGIS (container `geolume-worker:poc`), pytest, poppler (`pdfinfo`,
`pdftotext -bbox`), FastAPI, JS puro, `node:test`, Playwright, Chrome DevTools MCP.

**Spec:** `docs/superpowers/specs/2026-09-29-layouts-prancha-design.md`

## Restrições globais

- Não alterar `Dockerfile`, `docker-compose.yml`, `celery_app.py`, Redis, PostGIS; não apagar o volume PostgreSQL.
- `api.py`, `job.py` e `db.py` só mudam se um teste vermelho provar a necessidade.
- Conteúdo textual do memorial não muda; fontes: título 16, seções 12, texto 10, tabela 8 (memorial); tabela 7, propriedades 8 (mapa).
- Todo texto em rótulo QGIS passa por `texto_literal`. UI só `textContent`/`createElement`.
- Mensagens fixas: worker/API `"Legenda deve ser 'nenhuma', 'lateral' ou 'inferior'."`; navegador `"Layout da prancha inválido."`; aviso do mapa `"Exibidos K de M vértices. Demais vértices no memorial descritivo."`.
- Margem de segurança do mapa: 5 mm (área útil 5–292 × 5–205 mm).
- Área útil do memorial (A4 retrato): x 18–192 mm, y 15–260 mm; rodapé em y = 265 em toda página.
- TDD: cada teste novo roda vermelho antes da implementação.
- pytest: `MSYS_NO_PATHCONV=1 docker compose run --rm worker python3 -m pytest -q <alvo> > out.txt; echo exit=$?` (nunca `| tail`).
- JS: `cd tests-web && npm run test:unit`; E2E: `npm run test:e2e` (projetos `chrome-desktop` 1440×900 e `chrome-mobile` 390×844, stack real em `localhost:8000`).
- `index.html`, `render.js`, `app.js`, `memorial.py` são CRLF: preservar.

## Review Focus

1. Propriedade de 500 caracteres **sem espaço** → cortada com "…" no mapa; quebrada por caractere no memorial, dentro da margem. Teste: Tarefas 3 e 6.
2. Limite exato da tabela do mapa: o maior M que cabe inteiro não mostra aviso; M+1 mostra aviso e nenhuma linha passa de 205. Teste: Tarefa 3 (`test_limite_exato_da_tabela`).
3. `legenda` vazia (`""`) ou ausente no dict salvo → Padrão, não erro. Teste: Tarefa 1.
4. Título de seção do memorial sozinho no rodapé da página (órfão) → vai para a próxima página junto com o conteúdo. Teste: Tarefa 6 (`test_titulo_de_secao_nunca_orfao`, varre 40–120 vértices).
5. Texto `[% ... %]` numa propriedade longa que é cortada → continua literal no PDF. Teste: Tarefa 3.

---

### Tarefa 1: contrato `inferior` no worker e na API

**Arquivos:**
- Modificar: `worker/geolume_worker/prancha.py` (`LEGENDAS`, mensagem em `validar_prancha`)
- Testes: `worker/tests/test_prancha.py`, `worker/tests/test_api_prancha.py`

**Interfaces:**
- Produz: `LEGENDAS = ("nenhuma", "lateral", "inferior")`; `validar_prancha({"legenda": "inferior"}).legenda == "inferior"`.

- [ ] **Passo 1:** testes em `test_prancha.py`:
  - `test_aceita_os_tres_layouts` (parametrizado nos três).
  - `test_legenda_vazia_ou_ausente_e_padrao` (`""`, `None`, chave ausente → `"nenhuma"`).
  - `test_recusa_layout_desconhecido`, parametrizado com `"topo"`, `" lateral"`, `"LATERAL"`, `1`, `[]`, `"x" * 10_000`. Espera `InvalidInputError` com código `legenda_invalida` e a mensagem fixa, que não contém o valor.
- [ ] **Passo 2:** testes em `test_api_prancha.py`, seguindo o padrão do arquivo:
  - `legenda=inferior` → 202; o registro gravado tem `prancha["legenda"] == "inferior"` e o GET devolve o mesmo.
  - `legenda=topo` (com logo e GeoJSON válidos) → 422 com a mensagem fixa; nenhum arquivo novo em `inputs/` ou `logos/`, nenhum `create_db_job`, nenhum `delay`.
- [ ] **Passo 3:** rodar os dois arquivos → FAIL em `inferior` e na mensagem.
- [ ] **Passo 4:** implementar.
- [ ] **Passo 5:** rodar → PASS; rodar `test_job.py` → PASS (job antigo, `prancha=None`, segue Padrão).

### Tarefa 2: módulo de medida e verificador de PDF dos testes

**Arquivos:**
- Criar: `worker/geolume_worker/medida.py`
- Criar: `worker/tests/pdf_verif.py` (ajudantes de teste, sem testes próprios além dos abaixo)
- Criar: `worker/tests/poligonos.py` (gerador de GeoJSON)
- Testes: `worker/tests/test_medida.py`

**Interfaces:**
- Produz (`medida.py`, todos em mm, fonte DejaVu Sans, texto via `texto_literal`):
  - `medir(layout: QgsPrintLayout, texto: str, tamanho: float) -> tuple[float, float]`: largura e altura de um rótulo ajustado ao texto; o rótulo não é adicionado ao layout.
  - `quebrar_por_largura(layout, texto: str, tamanho: float, largura: float) -> list[str]`: guloso por palavras; palavra que sozinha excede a largura é quebrada por caractere; preserva parágrafos (`\n\n` gera linha vazia).
  - `cortar_para_caber(layout, texto: str, tamanho: float, largura: float) -> str`: devolve o texto intacto se couber; senão o maior prefixo (busca binária) + `"…"` que caiba.
  - `linhas_que_cabem(layout, cabecalho: list[str], linhas: list[str], tamanho: float, altura: float) -> int`: maior k (busca binária) tal que o rótulo `cabecalho + linhas[:k]` tem altura ≤ `altura`.
- Produz (`pdf_verif.py`, em pt, origem no topo):
  - `paginas(pdf) -> int` (via `pdfinfo`).
  - `palavras(pdf) -> list[Palavra]`, com `Palavra(pagina: int, x0, y0, x1, y1: float, texto: str)` via `pdftotext -bbox`. Página vem da ordem dos `<page>`, e cada `<page>` guarda `largura` e `altura`.
  - `fora_da_margem(pdf, margem_pt: float) -> list[Palavra]`.
  - `sobrepostas(pdf, tolerancia_pt2: float = 0.5) -> list[tuple[Palavra, Palavra]]`: pares na mesma página cuja interseção tem área > tolerância.
- Produz (`poligonos.py`): `gerar(destino: Path, n: int, propriedades: dict | None = None) -> Path`. Polígono regular de n vértices, raio ~100 m em torno de (-47.9, -15.8) em EPSG:4326, `nome_imovel` "Teste" por padrão.

- [ ] **Passo 1:** `test_medida.py`, com `qgis_app` e um layout vazio:
  - `quebrar_por_largura`: nenhuma linha medida passa da largura (texto de 500 caracteres com espaços e sem espaços, 10 pt, 174 mm); texto curto volta como uma linha igual.
  - `cortar_para_caber`: curto intacto; longo termina em "…" e mede ≤ largura; `"[% 1+1 %]" * 60` cortado continua começando com `"[% 1+1 %]"`.
  - `linhas_que_cabem`: com 7 pt e altura 45, resultado k satisfaz `medir(k) ≤ 45 < medir(k+1)`.
- [ ] **Passo 2:** rodar → FAIL (módulo não existe).
- [ ] **Passo 3:** implementar `medida.py`, `pdf_verif.py` e `poligonos.py`.
- [ ] **Passo 4:** rodar → PASS.
- [ ] **Passo 5:** gerar com `poligonos.gerar` a fixture `worker/tests/fixtures/poligono_30_longo.geojson`: 30 vértices, `nome_imovel`, `proprietario` e `municipio` com 500 caracteres em palavras, `uf` "DF". Usada pela Tarefa 8.

### Tarefa 3: mapa sem clipping (tabela limitada, aviso, propriedades cortadas)

**Arquivos:**
- Modificar: `worker/geolume_worker/layout.py` (`montar_mapa`; novas constantes de zona; `_vertex_table_text` aceita `limite: int | None`)
- Testes: `worker/tests/test_layout_limites.py` (novo)

**Interfaces:**
- Consome: `medida.linhas_que_cabem`, `medida.cortar_para_caber`, `pdf_verif.*`, `poligonos.gerar`.
- Produz, em `layout.py`:
  - `MARGEM = 5`, `PAGINA = (297, 210)`, `LIMITE_INFERIOR = 205`.
  - `COLUNA_DIREITA = (218, 287)`.
  - `TABELA_X = 10`, `TABELA_Y = 160`, `FAIXA_INFERIOR = (117, 210, 153, 205)` (x0, x1, y0, y1).
  - Aviso: rótulo 7 pt em `(10, fim_da_tabela + 1)` com o texto da restrição global.

- [ ] **Passo 1:** testes:
  - `test_tabela_com_muitos_vertices_cabe_na_pagina[14,16,30]`:
    - no layout, a caixa da tabela e a do aviso terminam em ≤ 205;
    - no PDF, `paginas == 1`, `fora_da_margem(pdf, 5 mm em pt) == []` e `sobrepostas == []`;
    - o texto tem `"Exibidos K de M vértices. Demais vértices no memorial descritivo."` com M correto e 1 ≤ K < M;
    - `V1`…`VK` presentes e `V{K+1}` ausente.
  - `test_seis_vertices_sem_aviso`: o texto não contém "Exibidos". O `pdftotext -layout` do `gleba_rural_exemplo` sem prancha é igual à referência `worker/tests/fixtures/mapa_padrao_6v.txt`, gravada com o código atual antes de alterar `layout.py`.
  - `test_limite_exato_da_tabela`: acha o maior M sem aviso (varrer 10–16). M passa sem aviso e com a última linha ≤ 205; M+1 passa com aviso.
  - `test_propriedade_longa_cortada[com_espacos, sem_espacos, expressao]`: valores de 500 caracteres. Toda caixa de rótulo de propriedade fica em x ≤ 287. O PDF não tem palavra fora da margem. A linha termina em "…". No caso `"[% env('PATH') %]" * 30`, o texto extraído contém `[%env('PATH')%]` e não contém `/usr/`.
- [ ] **Passo 2:** gerar `mapa_padrao_6v.txt` com o código atual; rodar os testes → FAIL em 14/16/30 e nas propriedades.
- [ ] **Passo 3:** implementar em `montar_mapa`:
  - tabela: `linhas_que_cabem` com altura `205 - 160 - (altura do aviso + 1)` quando nem todas cabem em `205 - 160`;
  - propriedades: `cortar_para_caber(..., 8, 69)` em cada linha.
- [ ] **Passo 4:** rodar este arquivo e `test_layout.py`, `test_layout_prancha.py`, `test_texto_literal.py` → PASS.

### Tarefa 4: legenda inferior e matriz de sobreposição

**Arquivos:**
- Modificar: `worker/geolume_worker/layout.py`. `_legenda_lateral` vira `_legenda(layout, prancha, x: float, y: float)`. Lateral chama em `(218, fim_da_escala + 5)`; inferior chama em `(120, 155)`: "Legenda" 9 pt em (120, 155), amostra 8×5 em (120, 162), "Limite do imóvel" 8 pt em (131, 162).
- Testes: `worker/tests/test_layout_limites.py`, `worker/tests/test_layout_prancha.py`

- [ ] **Passo 1:** testes:
  - `test_legenda_inferior_na_faixa_inferior`: amostra e rótulos dentro de `FAIXA_INFERIOR`; `x0 ≥ fim da tabela + 5`; `y0 ≥ fim do mapa`. Amostra com as cores da prancha. No raster, a cor do contorno aparece dentro do retângulo da amostra: converter mm → px a 150 dpi e recortar.
  - `test_matriz_sem_sobreposicao`, parametrizado pelo produto:
    - layout ∈ {nenhuma, lateral, inferior};
    - logo ∈ {não, sim};
    - textos ∈ {curtos, 100 caracteres};
    - vértices ∈ {6, 14, 30};
    - propriedades ∈ {normais, 500 caracteres};
    - cores: padrão nas combinações pares e `#1A2B3C`/`#00FF7F` nas ímpares.

    No layout: grupos (cabeçalho, logo, mapa, norte, propriedades, resumo, escala, tabela+aviso, legenda) sem interseção entre si e dentro de 5–292 × 5–205. No PDF: 1 página, `fora_da_margem == []`, `sobrepostas == []`. "Limite do imóvel" presente só em lateral/inferior. Picture do norte e scalebar presentes no layout. "Tabela de vértices" no texto.
  - Em `worker/tests/test_job.py`, `test_run_job_com_legenda_inferior`: `run_job(..., prancha={"legenda": "inferior"})` → o `mapa.pdf` contém "Limite do imóvel". `run_job(..., prancha={"legenda": "topo"})` → `InvalidInputError` sem criar o diretório do job.
- [ ] **Passo 2:** rodar → FAIL (inferior não desenha).
- [ ] **Passo 3:** implementar.
- [ ] **Passo 4:** rodar `test_layout_limites.py` e `test_layout_prancha.py` → PASS. Se a matriz passar de ~3 min, marcar os casos pesados de PDF com um subconjunto fixo (as 3 × 2 × 2 com 30 vértices) e manter a checagem de layout na matriz inteira.

### Tarefa 5: memorial — quebra por largura na identificação e na descrição

**Arquivos:**
- Modificar: `worker/geolume_worker/memorial.py`. `memorial_text` devolve os parágrafos sem `textwrap`, e a quebra passa a ser feita por `quebrar_por_largura` com 10 pt e 174 mm. A identificação também quebra por largura.
- Testes: `worker/tests/test_memorial.py` (novo)

- [ ] **Passo 1:** `test_textos_longos_dentro_da_margem[com_espacos, sem_espacos]`: `nome_imovel`, `proprietario` e `municipio` com 500 caracteres, 6 vértices. `fora_da_margem(pdf, margem)` vazio, com margem lateral 18 mm e topo/rodapé por página. Todas as palavras do valor aparecem no texto extraído, juntando os espaços.
- [ ] **Passo 2:** rodar → FAIL.
- [ ] **Passo 3:** implementar.
- [ ] **Passo 4:** rodar → PASS.

### Tarefa 6: memorial paginado

**Arquivos:**
- Modificar: `worker/geolume_worker/memorial.py` (`export_memorial_pdf` usa `_Paginador`)
- Testes: `worker/tests/test_memorial.py`

**Interfaces:**
- Produz (interno): `_Paginador(layout)` com `pagina: int`, `y: float`, `cabe(altura) -> bool`, `nova_pagina() -> None` (usa `pageCollection().addPage` com A4 retrato e põe o rodapé na página nova), `rotulo(texto, tamanho, x=18) -> QgsLayoutItemLabel` (posiciona com `attemptMove(QgsLayoutPoint(x, y), page=self.pagina)` e avança `y`).
- Regras (spec D7–D9):
  - tabela fatiada com `linhas_que_cabem`;
  - cada fatia repete a linha de cabeçalho;
  - página 1 usa o título "Quadro de vértices"; as seguintes usam "Quadro de vértices (continuação)";
  - "Descrição perimetral" começa em `max(145, fim_da_tabela + 8)` na página 1, e em `fim + 8` nas demais;
  - título de seção só é posto se couber junto com a primeira linha/fatia.

- [ ] **Passo 1:** testes:
  - `test_todos_os_vertices_no_memorial[6,14,16,30,300]`:
    - cada `V1…VM` aparece como palavra;
    - "perimetral" vem depois da última linha da tabela, por (página, y);
    - linha de cabeçalho ("Vértice") em toda página que tem linha de tabela;
    - `fora_da_margem == []` e `sobrepostas == []`;
    - "Documento preliminar gerado automaticamente pelo Geolume." em cada página;
    - `paginas ≥ 1`: 6 e 14 → 1 página; 300 → mais de 1.
  - `test_fontes_nao_diminuem`: tamanhos dos `QgsLayoutItemLabel` ∈ {16, 12, 10, 8}. Usar um hook de teste: `montar_memorial(summary) -> tuple[QgsProject, QgsPrintLayout]`, extraído de `export_memorial_pdf` como `montar_mapa` faz.
  - `test_seis_vertices_mesmas_posicoes`: com 6 vértices, "Quadro de vértices" em y = 88 e "Descrição perimetral" em y = 145, como hoje.
  - `test_titulo_de_secao_nunca_orfao`: para M em `range(40, 121, 4)`, nenhum "Quadro de vértices (continuação)" e nenhum "Descrição perimetral" é o último rótulo da sua página.
  - `test_descricao_longa_continua_na_proxima_pagina`: nome de 3000 caracteres em palavras. Descrição dividida em ≥ 2 páginas, sem sobreposição com o rodapé.
- [ ] **Passo 2:** rodar → FAIL em 16/30/300.
- [ ] **Passo 3:** implementar `montar_memorial` e `_Paginador`.
- [ ] **Passo 4:** rodar `test_memorial.py`, `test_texto_literal.py`, `test_job.py` → PASS.

### Tarefa 7: navegador — lista de layouts e validação

**Arquivos:**
- Modificar: `worker/web/js/prancha.js`
- Teste: `tests-web/unit/prancha.test.mjs`

**Interfaces:**
- Produz:
  - `LAYOUTS`, lista congelada de `{ valor, nome, descricao }`:
    - `nenhuma` — "Padrão" — "Mapa, dados do imóvel, norte, escala e tabela de vértices.";
    - `lateral` — "Legenda lateral" — "Legenda na coluna direita, abaixo da escala.";
    - `inferior` — "Legenda inferior" — "Legenda abaixo do mapa, ao lado da tabela.".
  - `validatePrancha` aceita os três valores. Para outro valor devolve `{ ok: false, field: "legenda", message: "Layout da prancha inválido." }`.

- [ ] **Passo 1:** testes:
  - aceita os três valores;
  - recusa `"topo"`, `" lateral"`, `"LATERAL"`;
  - `""` vira `nenhuma`;
  - `pranchaFields` omite `legenda` quando é `nenhuma` e inclui `["legenda", "inferior"]`;
  - `LAYOUTS.map(l => l.valor)` é igual à lista do Python (`nenhuma`, `lateral`, `inferior`).
- [ ] **Passo 2:** `npm run test:unit` → FAIL. **Passo 3:** implementar. **Passo 4:** → PASS.

### Tarefa 8: cartões de layout, miniatura na pré-visualização e fluxo da UI

**Arquivos:**
- Modificar:
  - `worker/web/index.html`: o `<select>` vira `<fieldset data-testid="prancha-layouts">` com `<legend>Layout da prancha</legend>` e três `<label class="prancha-layout-card">`. Cada cartão tem:
    - `<input type="radio" name="prancha-legenda" value="…" data-testid="prancha-layout-<valor>">` (o de `nenhuma` vem `checked`);
    - `<span class="prancha-mini" data-layout="…" aria-hidden="true">` com divs `mini-mapa`, `mini-coluna`, `mini-tabela`, `mini-legenda`;
    - nome e descrição vindos de `LAYOUTS`.

    No `prancha-head` entra `<span class="prancha-mini" data-testid="prancha-miniatura" data-layout="nenhuma">`.
  - `worker/web/styles.css`:
    - cartões em grid de 3 colunas no desktop e 1 coluna abaixo de 480 px;
    - foco visível e cartão marcado com borda;
    - miniatura em proporção 297/210;
    - `mini-legenda` posicionada por `[data-layout]`.
  - `worker/web/js/render.js`:
    - `renderPranchaHead` define `data-layout` da miniatura (`view?.legenda || "nenhuma"`);
    - mostra `prancha-legenda` para `lateral` e `inferior`, com `data-posicao` igual ao valor.
  - `worker/web/js/app.js`:
    - `PRANCHA_INPUTS.legenda` sai;
    - `pranchaValues()` lê o valor de `input[name="prancha-legenda"]:checked` (sem nenhum marcado → `""`);
    - os rádios disparam `pranchaChanged` no evento `change`;
    - `resetPrancha` marca `nenhuma`.
- Teste: `tests-web/e2e/prancha.spec.mjs` (trocar `selectOption` por `check()` nos testes existentes; acrescentar os abaixo)

- [ ] **Passo 1:** testes E2E (rodam nos dois projetos, desktop e mobile):
  - `cartões de layout`: três rádios com nome acessível ("Padrão", "Legenda lateral", "Legenda inferior") dentro do grupo "Layout da prancha". Padrão marcado. Seta para a direita a partir de Padrão marca Legenda lateral.
  - `miniatura acompanha o layout`: marcar cada um → `prancha-miniatura` tem o `data-layout` correspondente. `prancha-legenda` visível só em lateral/inferior, com `data-posicao`.
  - `envio leva a legenda escolhida`: interceptar o POST e conferir `name="legenda"` com `inferior` no corpo multipart. Com Padrão, `legenda` ausente (teste já existente continua).
  - `histórico mostra o layout do job`: job enviado com `inferior` → ao selecionar no histórico, miniatura `inferior`. Job antigo (prancha nula) → `nenhuma`.
  - `rádio adulterado bloqueia envio`: `evaluate` troca o `value` do rádio marcado para `"topo"` e dispara `change` → `prancha-message` mostra "Layout da prancha inválido." e `upload-submit` fica desabilitado. Nenhum POST sai.
  - `restaurar padrão volta ao Padrão`.
  - `sem rolagem horizontal`: `document.documentElement.scrollWidth <= innerWidth` com o formulário aberto.
  - `mapa.pdf e memorial.pdf reais por layout` (stack real, `test.slow()`):
    - enviar `worker/tests/fixtures/poligono_30_longo.geojson` (30 vértices e propriedades de 500 caracteres, gerado por `poligonos.gerar` na Tarefa 2 e salvo como fixture) com logo, projeto e responsável de 100 caracteres, cores customizadas e cada layout;
    - baixar os dois PDFs;
    - com o `execFileSync` já usado no arquivo: `pdfinfo` do mapa = 1 página;
    - `pdftotext` do memorial contém `V1`…`V30`;
    - o mapa contém "Demais vértices no memorial descritivo".
- [ ] **Passo 2:** `npm run test:e2e > out.txt; echo exit=$?` → FAIL nos novos.
- [ ] **Passo 3:** implementar.
- [ ] **Passo 4:** → PASS em todos (existentes + novos).

### Tarefa 9: verificação final, DevTools, README e revisão Codex

**Arquivos:**
- Modificar: `worker/README.md` (seções "Layouts da prancha" e "Memorial paginado")

- [ ] **Passo 1:** rodar pytest inteiro, `test:unit` e `test:e2e`, todos com exit code capturado → 0.
- [ ] **Passo 2:** Chrome DevTools MCP em `localhost:8000`, 1440×900 e 390×844:
  - screenshot dos cartões e da pré-visualização em cada layout;
  - `list_console_messages` sem erros;
  - snapshot de acessibilidade com o grupo "Layout da prancha" e três rádios nomeados.
- [ ] **Passo 3:** abrir no DevTools um `mapa.pdf` e um `memorial.pdf` (30 vértices) de cada layout e conferir visualmente.
- [ ] **Passo 4:** atualizar o README.
- [ ] **Passo 5:** revisão independente com o Codex, somente leitura, sobre os arquivos alterados, com a spec e este plano como referência.
- [ ] **Passo 6:** para cada achado, reproduzir com teste vermelho; só então corrigir; rodar as três suítes de novo.
- [ ] **Passo 7:** se aprovado pelo usuário, registrar no AI Brain o estado do slice.

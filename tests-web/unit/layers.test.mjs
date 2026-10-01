import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  CATALOGO_VAZIO,
  atribuicaoHtml,
  basemaps,
  camadaAtivavel,
  grupos,
  parseCatalogo,
  legendItems,
  withBase,
} from "../../worker/web/js/layers.js";

// ---- Catálogo servido pela API (GET /camadas) -----------------------------------------

const CATALOGO = JSON.parse(
  readFileSync(new URL("../../worker/tests/fixtures/catalogo_publico.json", import.meta.url), "utf-8"),
);
const copia = () => structuredClone(CATALOGO);
const camadaDe = (catalogo, id) => catalogo.camadas.find((c) => c.id === id);
const ids = (camadas) => camadas.map((c) => c.id);

test("parseCatalogo aceita o catálogo real da API", () => {
  const catalogo = parseCatalogo(copia());
  assert.equal(catalogo.camadas.length, 9);
  assert.deepEqual(ids(catalogo.estilos), ["padrao", "tecnico", "pb"]);
  assert.equal(catalogo.alfaPadrao, 0.35);
  assert.equal(camadaDe(catalogo, "ruas_osm").url, "https://tile.openstreetmap.org/{z}/{x}/{y}.png");
});

for (const ruim of [null, undefined, "x", [], {}, { camadas: "x", estilos: [] }, { camadas: [], estilos: {} }]) {
  test(`parseCatalogo recusa corpo malformado ${JSON.stringify(ruim)}`, () => {
    assert.equal(parseCatalogo(ruim), null);
  });
}

test("parseCatalogo recusa alfa padrão fora da regra", () => {
  for (const alfa of [2, "0,5", null, NaN]) assert.equal(parseCatalogo({ ...copia(), alfa_padrao: alfa }), null);
});

for (const url of ["http://tile.openstreetmap.org/{z}/{x}/{y}.png", "javascript:alert(1)", "//evil.test/{z}", "data:x"]) {
  test(`parseCatalogo descarta camada com url ${url}`, () => {
    const corpo = copia();
    camadaDe(corpo, "ruas_osm").url = url;
    const catalogo = parseCatalogo(corpo);
    assert.equal(camadaDe(catalogo, "ruas_osm"), undefined);
    assert.equal(catalogo.camadas.length, 8);
  });
}

test("parseCatalogo descarta camada indisponível que traga URL", () => {
  const corpo = copia();
  camadaDe(corpo, "satelite").url = "https://satelite.exemplo/{z}/{x}/{y}.png";
  assert.equal(camadaDe(parseCatalogo(corpo), "satelite"), undefined);
});

test("parseCatalogo descarta camada e estilo com campos inválidos", () => {
  const corpo = copia();
  camadaDe(corpo, "hidrografia").id = "Hidro grafia";
  corpo.estilos[1].contorno = "red";
  corpo.estilos[2].espessura_px = -1;
  const catalogo = parseCatalogo(corpo);
  assert.equal(catalogo.camadas.length, 8);
  assert.deepEqual(ids(catalogo.estilos), ["padrao"]);
});

test("grupos separa as 9 camadas em 3 disponíveis, 4 dependentes de fonte e 2 planejadas", () => {
  const { disponiveis, dependemFonte, planejadas } = grupos(parseCatalogo(copia()));
  assert.deepEqual(ids(disponiveis), ["ruas_osm", "nenhum", "poligono"]);
  assert.deepEqual(ids(dependemFonte), ["satelite", "hidrografia", "rodovias", "limites_municipais"]);
  assert.deepEqual(ids(planejadas), ["topografia", "edificacoes"]);
});

test("camadaAtivavel: só o que está disponível; satélite e fontes oficiais não", () => {
  const catalogo = parseCatalogo(copia());
  for (const id of ["ruas_osm", "nenhum", "poligono"]) assert.equal(camadaAtivavel(camadaDe(catalogo, id)), true, id);
  for (const id of ["satelite", "limites_municipais", "hidrografia", "rodovias", "topografia", "edificacoes"]) {
    assert.equal(camadaAtivavel(camadaDe(catalogo, id)), false, id);
  }
  assert.equal(camadaAtivavel(null), false);
  assert.equal(camadaAtivavel({ id: "x", situacao: "disponivel", tipo: "raster", url: null }), false);
});

test("basemaps: as camadas do grupo base, satélite incluso (desabilitado)", () => {
  assert.deepEqual(ids(basemaps(parseCatalogo(copia()))), ["ruas_osm", "nenhum", "satelite"]);
});

test("atribuicaoHtml da OSM: link seguro para os termos", () => {
  const osm = camadaDe(CATALOGO, "ruas_osm").fonte.atribuicao;
  assert.equal(
    atribuicaoHtml(osm),
    '<a href="https://www.openstreetmap.org/copyright" rel="noopener noreferrer" target="_blank">© Contribuidores do OpenStreetMap</a>',
  );
});

test("atribuicaoHtml escapa texto e URL", () => {
  const html = atribuicaoHtml({ texto: `<img src=x onerror=alert(1)> & "'`, url: `https://x.test/"><script>` });
  assert.doesNotMatch(html, /<img|<script|"></);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt; &amp; &quot;&#39;/);
  assert.match(html, /href="https:\/\/x\.test\/&quot;&gt;&lt;script&gt;"/);
});

test("atribuicaoHtml sem https vira só texto escapado", () => {
  for (const url of ["javascript:alert(1)", "http://x.test", "", null]) {
    assert.equal(atribuicaoHtml({ texto: "<b>Fonte</b>", url }), "&lt;b&gt;Fonte&lt;/b&gt;");
  }
  assert.equal(atribuicaoHtml(null), "");
});

test("legendItems com catálogo: ruas_osm traz o aviso de que não entra no PDF", () => {
  const catalogo = parseCatalogo(copia());
  assert.deepEqual(legendItems({ base: "ruas_osm", polygon: true }, catalogo), [
    { kind: "base", text: "Mapa-base: Mapa de ruas (OpenStreetMap)" },
    { kind: "aviso", text: "Somente na pré-visualização — não entra no PDF" },
    { kind: "poligono", text: "Polígono do imóvel" },
  ]);
});

test("legendItems com catálogo: falha dos tiles, sem mapa-base e catálogo vazio", () => {
  const catalogo = parseCatalogo(copia());
  assert.deepEqual(legendItems({ base: "ruas_osm", polygon: false }, catalogo, { tilesFailed: true }), [
    { kind: "base", text: "Mapa-base: Mapa de ruas (OpenStreetMap) — indisponível" },
    { kind: "aviso", text: "Somente na pré-visualização — não entra no PDF" },
  ]);
  assert.deepEqual(legendItems({ base: "nenhum", polygon: false }, catalogo), [{ kind: "base", text: "Mapa-base: nenhum" }]);
  // Base que não veio no catálogo (ou não ativável) não aparece como ativa.
  assert.deepEqual(legendItems({ base: "ruas_osm", polygon: true }, CATALOGO_VAZIO), [
    { kind: "base", text: "Mapa-base: nenhum" },
    { kind: "poligono", text: "Polígono do imóvel" },
  ]);
  assert.deepEqual(legendItems({ base: "satelite", polygon: false }, catalogo), [{ kind: "base", text: "Mapa-base: nenhum" }]);
});

test("CATALOGO_VAZIO (catálogo falhou): só sem mapa-base e polígono, sem nenhuma URL", () => {
  assert.deepEqual(ids(CATALOGO_VAZIO.camadas), ["nenhum", "poligono"]);
  assert.deepEqual(CATALOGO_VAZIO.estilos, []);
  assert.equal(CATALOGO_VAZIO.alfaPadrao, 0.35);
  assert.ok(CATALOGO_VAZIO.camadas.every((c) => c.url === null));
  assert.ok(Object.isFrozen(CATALOGO_VAZIO) && Object.isFrozen(CATALOGO_VAZIO.camadas));
});

test("withBase com catálogo: só mapa-base ativável", () => {
  const catalogo = parseCatalogo(copia());
  const view = { base: "ruas_osm", polygon: true };
  assert.deepEqual(withBase(view, "nenhum", catalogo), { ...view, base: "nenhum" });
  for (const id of ["satelite", "poligono", "hidrografia", "inexistente", "__proto__"]) {
    assert.equal(withBase(view, id, catalogo), view, id);
  }
  const vazio = { base: "nenhum", polygon: true };
  assert.equal(withBase(vazio, "ruas_osm", CATALOGO_VAZIO), vazio);
});

// ---- Só o catálogo real: sem compatibilidade com a lista fixa antiga -------------------

test("layers.js não exporta mais a lista fixa antiga nem os helpers de transparência", async () => {
  const modulo = await import("../../worker/web/js/layers.js");
  for (const nome of ["BASEMAPS", "CATALOGO_LEGADO", "DEFAULT_VIEW", "findBasemap", "opacityFromPercent", "percentFromOpacity"]) {
    assert.equal(nome in modulo, false, nome);
  }
});

test("withBase sem catálogo não troca o mapa-base", () => {
  const view = { base: "nenhum", polygon: true };
  for (const catalogo of [undefined, null, {}]) {
    assert.equal(withBase(view, "ruas_osm", catalogo), view);
    assert.equal(withBase(view, "ruas", catalogo), view); // id da lista antiga
  }
});

test("legendItems sem catálogo: nenhum mapa-base, só o polígono", () => {
  for (const catalogo of [undefined, null, {}]) {
    assert.deepEqual(legendItems({ base: "ruas", polygon: true }, catalogo, { tilesFailed: true }), [
      { kind: "base", text: "Mapa-base: nenhum" },
      { kind: "poligono", text: "Polígono do imóvel" },
    ]);
  }
});

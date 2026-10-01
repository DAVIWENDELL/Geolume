import { test } from "node:test";
import assert from "node:assert/strict";
import { createMapView } from "../../worker/web/js/map.js";
import { readFileSync } from "node:fs";
import { CATALOGO_LEGADO, CATALOGO_VAZIO, DEFAULT_VIEW, atribuicaoHtml, parseCatalogo } from "../../worker/web/js/layers.js";
import { polygonStyle } from "../../worker/web/js/prancha.js";

const alfa8de = (alfa) => Math.floor(alfa * 255 + 0.5);
const GEOMETRY = { type: "Polygon", coordinates: [[[-47.9, -15.8], [-47.9, -15.79], [-47.89, -15.79], [-47.9, -15.8]]] };
const BOUNDS = [[-15.8, -47.9], [-15.79, -47.89]];
const OUTRO = [[-10, -40], [-9, -39]];

/** Leaflet falso: registra o que o mapa pede, sem DOM. */
function fakeLeaflet() {
  const log = { maps: [], tiles: [], shapes: [] };
  const layer = (extra) => {
    const handlers = {};
    const obj = {
      onMap: false,
      handlers,
      addTo() { obj.onMap = true; return obj; },
      remove() { obj.onMap = false; return obj; },
      on(event, fn) { (handlers[event] ??= []).push(fn); return obj; },
      fire(event) { for (const fn of handlers[event] ?? []) fn({}); },
      ...extra,
    };
    return obj;
  };
  const L = {
    map(container, options) {
      const map = { container, options, fits: [], removed: false, panes: {},
        createPane(name) { map.panes[name] = { style: {} }; return map.panes[name]; },
        getPane(name) { return map.panes[name]; },
        invalidateSize() {}, fitBounds(bounds, opts) { map.fits.push({ bounds, opts }); }, remove() { map.removed = true; } };
      log.maps.push(map);
      return map;
    },
    tileLayer(url, options) {
      const t = layer({ url, options });
      log.tiles.push(t);
      return t;
    },
    geoJSON(geometry, options) {
      const s = layer({ geometry, options, styles: [], setStyle(style) { s.styles.push(style); return s; } });
      log.shapes.push(s);
      return s;
    },
  };
  return { L, log };
}

function setup(opcoes = {}) {
  const { L, log } = fakeLeaflet();
  const container = { hidden: true };
  const changes = [];
  // Os testes antigos (ids ruas/nenhum/satelite) usam o CATALOGO_LEGADO; os novos passam o catálogo real.
  const view = createMapView(container, { leaflet: L, onChange: (s) => changes.push(s), catalogo: CATALOGO_LEGADO, ...opcoes });
  const tilesOn = () => log.tiles.filter((t) => t.onMap);
  const shape = () => log.shapes.at(-1);
  return { L, log, container, changes, view, tilesOn, shape };
}

test("sem geometria não cria mapa nem pede tiles", () => {
  const { log, view } = setup();
  view.setBase("nenhum");
  view.setOpacity(0.5);
  assert.equal(log.maps.length, 0);
  assert.equal(log.tiles.length, 0);
  assert.equal(view.state.visible, false);
});

test("primeira geometria: mapa de ruas padrão, polígono com contorno e enquadramento", () => {
  const { log, container, view, tilesOn, shape } = setup();
  assert.equal(view.show(GEOMETRY, BOUNDS), true);
  assert.equal(container.hidden, false);
  assert.equal(tilesOn().length, 1);
  assert.equal(tilesOn()[0].url, "https://tile.openstreetmap.org/{z}/{x}/{y}.png");
  assert.match(tilesOn()[0].options.attribution, /OpenStreetMap/);
  assert.equal(shape().onMap, true);
  assert.equal(shape().options.interactive, false);
  assert.equal(shape().options.style.fillOpacity, 89 / 255); // alfa8(0.35) / 255, igual ao mapa.pdf
  assert.equal(shape().options.style.opacity, 1);
  assert.equal(shape().options.style.weight, 3);
  assert.equal(shape().options.style.color, "#C80000"); // mesmas cores padrão do mapa.pdf
  assert.equal(shape().options.style.fillColor, "#FFC800");
  assert.deepEqual(log.maps[0].fits.at(-1).bounds, BOUNDS);
  assert.deepEqual(view.state, { visible: true, view: DEFAULT_VIEW, tilesFailed: false });
});

test("sem mapa-base remove os tiles e o polígono continua", () => {
  const { view, tilesOn, shape } = setup();
  view.show(GEOMETRY, BOUNDS);
  assert.equal(view.setBase("nenhum"), true);
  assert.equal(tilesOn().length, 0);
  assert.equal(shape().onMap, true);
  assert.equal(view.state.view.base, "nenhum");
  view.setBase("ruas");
  assert.equal(tilesOn().length, 1); // uma camada por vez
});

test("satélite desabilitado não troca a camada nem cria tiles", () => {
  const { log, view, tilesOn } = setup();
  view.show(GEOMETRY, BOUNDS);
  const antes = log.tiles.length;
  assert.equal(view.setBase("satelite"), false);
  assert.equal(log.tiles.length, antes);
  assert.equal(tilesOn().length, 1);
  assert.equal(view.state.view.base, "ruas");
});

test("ocultar e mostrar o polígono", () => {
  const { view, shape } = setup();
  view.show(GEOMETRY, BOUNDS);
  view.setPolygonVisible(false);
  assert.equal(shape().onMap, false);
  assert.equal(view.state.view.polygon, false);
  view.setPolygonVisible(true);
  assert.equal(shape().onMap, true);
});

test("transparência muda só o preenchimento; o contorno fica", () => {
  const { view, shape } = setup();
  view.show(GEOMETRY, BOUNDS);
  view.setOpacity(0);
  assert.equal(shape().styles.at(-1).fillOpacity, 0);
  assert.equal(shape().styles.at(-1).opacity, 1);
  view.setOpacity(2);
  assert.equal(shape().styles.at(-1).fillOpacity, 1);
  assert.equal(view.state.view.opacity, 1);
});

test("escolhas valem para a próxima geometria (troca de job)", () => {
  const { view, shape } = setup();
  view.show(GEOMETRY, BOUNDS);
  view.setPolygonVisible(false);
  view.setOpacity(0.5);
  view.show(GEOMETRY, OUTRO);
  assert.equal(shape().onMap, false);
  assert.equal(shape().options.style.fillOpacity, alfa8de(0.5) / 255);
  view.setPolygonVisible(true);
  assert.equal(shape().onMap, true);
});

test("enquadrar volta para a geometria atual, mesmo com o polígono oculto", () => {
  const { log, view } = setup();
  assert.equal(view.fit(), false);
  view.show(GEOMETRY, BOUNDS);
  view.show(GEOMETRY, OUTRO);
  view.setPolygonVisible(false);
  assert.equal(view.fit(), true);
  assert.deepEqual(log.maps[0].fits.at(-1).bounds, OUTRO);
  view.clear();
  assert.equal(view.fit(), false);
});

test("falha de tiles é avisada e some ao trocar de camada", () => {
  const { view, tilesOn, changes, shape } = setup();
  view.show(GEOMETRY, BOUNDS);
  tilesOn()[0].fire("tileerror");
  assert.equal(view.state.tilesFailed, true);
  assert.equal(changes.at(-1).tilesFailed, true);
  assert.equal(shape().onMap, true); // o polígono não depende dos tiles
  view.setBase("nenhum");
  assert.equal(view.state.tilesFailed, false);
});

test("erro de tiles de uma camada antiga não marca a atual", () => {
  const { log, view } = setup();
  view.show(GEOMETRY, BOUNDS);
  const antiga = log.tiles[0];
  view.setBase("nenhum");
  view.setBase("ruas");
  antiga.fire("tileerror");
  assert.equal(view.state.tilesFailed, false);
});

test("clear esconde o mapa; reset volta ao padrão e descarta o mapa", () => {
  const { log, container, view, changes } = setup();
  view.show(GEOMETRY, BOUNDS);
  view.setBase("nenhum");
  view.setPolygonVisible(false);
  view.setOpacity(0.7);
  view.clear();
  assert.equal(container.hidden, true);
  assert.equal(view.state.visible, false);
  assert.equal(changes.at(-1).visible, false);
  view.reset();
  assert.equal(log.maps[0].removed, true);
  assert.deepEqual(view.state, { visible: false, view: DEFAULT_VIEW, tilesFailed: false });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(log.maps.length, 2);
  assert.equal(log.tiles.filter((t) => t.onMap).length, 1);
});

test("sem Leaflet: show devolve false e nada quebra", () => {
  const container = { hidden: true };
  const view = createMapView(container, { leaflet: null });
  const original = globalThis.L;
  delete globalThis.L;
  try {
    assert.equal(view.available, false);
    assert.equal(view.show(GEOMETRY, BOUNDS), false);
    assert.equal(view.setBase("nenhum"), true);
    view.setPolygonVisible(false);
    assert.equal(view.fit(), false);
    view.reset();
    assert.equal(container.hidden, true);
  } finally {
    if (original) globalThis.L = original;
  }
});

test("exceção interna do Leaflet no show devolve false, esconde o mapa e permite tentar de novo", () => {
  for (const etapa of ["map", "geoJSON", "fitBounds"]) {
    const { L, log, container, view } = setup();
    const original = L[etapa];
    let falhar = true;
    if (etapa === "fitBounds") {
      L.map = ((make) => (...args) => {
        const map = make(...args);
        map.fitBounds = () => { if (falhar) throw new Error("fitBounds"); };
        return map;
      })(L.map);
    } else {
      L[etapa] = (...args) => { if (falhar) throw new Error(etapa); return original(...args); };
    }
    assert.equal(view.show(GEOMETRY, BOUNDS), false, etapa);
    assert.equal(container.hidden, true, etapa);
    assert.equal(view.state.visible, false, etapa);
    assert.ok(log.maps.every((m) => m.removed), etapa); // mapa parcial descartado
    falhar = false;
    assert.equal(view.show(GEOMETRY, BOUNDS), true, etapa);
    assert.equal(container.hidden, false, etapa);
  }
});

test("exceção ao criar a camada de tiles vira aviso de falha, sem quebrar", () => {
  const { L, view, shape } = setup();
  view.show(GEOMETRY, BOUNDS);
  L.tileLayer = () => { throw new Error("tileLayer"); };
  view.setBase("nenhum");
  assert.doesNotThrow(() => view.setBase("ruas"));
  assert.equal(view.state.view.base, "ruas");
  assert.equal(view.state.tilesFailed, true);
  assert.equal(shape().onMap, true);
});

test("setColors pinta o polígono atual e os próximos; reset volta às cores padrão", () => {
  const { view, shape } = setup();
  view.setColors({ contorno: "#112233", preenchimento: "#445566" });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(shape().options.style.color, "#112233");
  assert.equal(shape().options.style.fillColor, "#445566");
  view.setColors({ contorno: "#000000", preenchimento: "#FFFFFF" });
  assert.equal(shape().styles.at(-1).color, "#000000");
  assert.equal(shape().styles.at(-1).fillColor, "#FFFFFF");
  view.reset();
  view.show(GEOMETRY, BOUNDS);
  assert.equal(shape().options.style.color, "#C80000");
  assert.equal(shape().options.style.fillColor, "#FFC800");
});

test("setColors ignora cor fora de #RRGGBB", () => {
  const { view, shape } = setup();
  view.setColors({ contorno: "red;x", preenchimento: "url(javascript:1)" });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(shape().options.style.color, "#C80000");
  assert.equal(shape().options.style.fillColor, "#FFC800");
});

// ---- Task 6: catálogo, panes e estilo único ------------------------------------------

const CORPO = JSON.parse(readFileSync(new URL("../../worker/tests/fixtures/catalogo_publico.json", import.meta.url), "utf-8"));
const catalogo = () => parseCatalogo(structuredClone(CORPO));
const OSM = CORPO.camadas.find((c) => c.id === "ruas_osm");
const semClasse = (estilo) => ({ ...estilo, className: undefined });

test("pane do polígono acima do mapa-base (z-index pela ordem do catálogo)", () => {
  const { log, view, tilesOn, shape } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  const panes = log.maps[0].panes;
  assert.equal(tilesOn()[0].options.pane, "geolume-base");
  assert.equal(shape().options.pane, "geolume-poligono");
  assert.equal(Number(panes["geolume-base"].style.zIndex), 200 + OSM.ordem);
  assert.equal(Number(panes["geolume-poligono"].style.zIndex), 400 + 100);
});

test("mapa-base com ordem alta continua abaixo do polígono", () => {
  const cat = catalogo();
  cat.camadas = cat.camadas.map((c) => (c.id === "ruas_osm" ? { ...c, ordem: 900 } : c));
  const { log, view } = setup({ catalogo: cat });
  view.show(GEOMETRY, BOUNDS);
  const panes = log.maps[0].panes;
  assert.ok(Number(panes["geolume-poligono"].style.zIndex) > Number(panes["geolume-base"].style.zIndex));
});

test("catálogo real: ruas_osm é o mapa-base inicial, com a atribuição escapada da OSM", () => {
  const { view, tilesOn } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(view.state.view.base, "ruas_osm");
  assert.equal(tilesOn().length, 1);
  assert.equal(tilesOn()[0].url, OSM.url);
  assert.equal(tilesOn()[0].options.maxZoom, 19);
  assert.equal(tilesOn()[0].options.attribution, atribuicaoHtml(OSM.fonte.atribuicao));
});

test("atribuição da OSM só enquanto o mapa OSM está ativo", () => {
  const { log, view, tilesOn } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(view.setBase("nenhum"), true);
  assert.equal(tilesOn().length, 0); // a camada sai do mapa e leva a atribuição junto
  assert.equal(log.tiles.length, 1);
  view.setBase("ruas_osm");
  assert.equal(tilesOn().length, 1);
});

test("setBase recusa camada não ativável e não cria tileLayer", () => {
  const { log, view, tilesOn } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  for (const id of ["satelite", "limites_municipais", "hidrografia", "topografia", "poligono", "inexistente", "__proto__", "constructor"]) {
    assert.equal(view.setBase(id), false, id);
  }
  assert.equal(log.tiles.length, 1);
  assert.equal(tilesOn()[0].url, OSM.url);
  assert.equal(view.state.view.base, "ruas_osm");
});

test("sem catálogo (GET /camadas falhou) nenhum tileLayer é criado", () => {
  const { log, view, shape } = setup({ catalogo: CATALOGO_VAZIO });
  assert.equal(view.show(GEOMETRY, BOUNDS), true);
  assert.equal(log.tiles.length, 0);
  assert.equal(view.state.view.base, "nenhum");
  assert.equal(view.setBase("ruas_osm"), false);
  assert.equal(view.setBase("ruas"), false);
  assert.equal(log.tiles.length, 0);
  assert.equal(shape().onMap, true); // o polígono continua, com o estilo padrão
  assert.equal(shape().options.style.weight, 3);
});

test("setCatalogo(CATALOGO_VAZIO) depois da OSM remove os tiles e volta para sem mapa-base", () => {
  const { log, view, tilesOn, shape, changes } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  view.setCatalogo(CATALOGO_VAZIO);
  assert.equal(tilesOn().length, 0);
  assert.equal(log.tiles.length, 1);
  assert.equal(view.state.view.base, "nenhum");
  assert.equal(changes.at(-1).view.base, "nenhum");
  assert.equal(shape().onMap, true);
});

test("catálogo que não é catálogo vira CATALOGO_VAZIO", () => {
  for (const ruim of [null, {}, { camadas: "x" }]) {
    const { log, view } = setup({ catalogo: ruim });
    view.show(GEOMETRY, BOUNDS);
    assert.equal(log.tiles.length, 0);
    assert.equal(view.state.view.base, "nenhum");
    view.setCatalogo(ruim);
    assert.equal(log.tiles.length, 0);
  }
});

for (const url of ["http://tile.openstreetmap.org/{z}/{x}/{y}.png", "javascript:alert(1)", "data:image/png;base64,x", "//evil.test/{z}/{x}/{y}.png", " https://x.test/{z}"]) {
  test(`camada com url insegura ${url} (sem passar pelo parseCatalogo) não vira tileLayer`, () => {
    const cat = catalogo();
    cat.camadas = cat.camadas.map((c) => (c.id === "ruas_osm" ? { ...c, url } : c));
    const { log, view } = setup({ catalogo: cat });
    view.show(GEOMETRY, BOUNDS);
    assert.equal(view.setBase("ruas_osm"), false);
    assert.equal(log.tiles.length, 0);
  });
}

test("camada indisponível com URL https (sem passar pelo parseCatalogo) não vira tileLayer", () => {
  const cat = catalogo();
  cat.camadas = cat.camadas.map((c) => (c.id === "satelite" ? { ...c, url: "https://sat.test/{z}/{x}/{y}.png" } : c));
  const { log, view } = setup({ catalogo: cat });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(view.setBase("satelite"), false);
  assert.ok(log.tiles.every((t) => t.url === OSM.url));
});

const PRANCHA_TECNICO = { estilo: "tecnico", cor_contorno: "#1F2937", cor_preenchimento: "#9CA3AF", alfa_preenchimento: 0.6 };

test("setStyle aplica polygonStyle de uma vez (cores, espessura e alfa juntos)", () => {
  const cat = catalogo();
  const { view, shape } = setup({ catalogo: cat });
  view.show(GEOMETRY, BOUNDS);
  const antes = shape().styles.length;
  view.setStyle(PRANCHA_TECNICO);
  assert.equal(shape().styles.length, antes + 1); // uma única chamada
  assert.deepEqual(shape().styles.at(-1), polygonStyle(PRANCHA_TECNICO, cat.estilos));
  assert.equal(shape().styles.at(-1).weight, 2);
  assert.equal(shape().styles.at(-1).fillOpacity, 153 / 255);
  assert.equal(view.state.view.opacity, 0.6);
});

test("setStyle antes da geometria vale para o polígono desenhado e para o próximo job", () => {
  const cat = catalogo();
  const { view, shape } = setup({ catalogo: cat });
  view.setStyle(PRANCHA_TECNICO);
  view.show(GEOMETRY, BOUNDS);
  assert.deepEqual(semClasse(shape().options.style), semClasse(polygonStyle(PRANCHA_TECNICO, cat.estilos)));
  assert.equal(shape().options.style.className, "geolume-poligono");
  view.show(GEOMETRY, OUTRO);
  assert.equal(shape().options.style.weight, 2);
});

test("cor alterada mantém a espessura do estilo", () => {
  const { view, shape } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  view.setStyle({ ...PRANCHA_TECNICO, cor_contorno: "#FF00FF" });
  assert.equal(shape().styles.at(-1).color, "#FF00FF");
  assert.equal(shape().styles.at(-1).weight, 2);
});

test("alfa 0 mantém contorno com opacity 1", () => {
  const { view, shape } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  view.setStyle({ ...PRANCHA_TECNICO, alfa_preenchimento: 0 });
  const estilo = shape().styles.at(-1);
  assert.equal(estilo.fillOpacity, 0);
  assert.equal(estilo.opacity, 1);
  assert.ok(estilo.weight > 0);
  assert.equal(estilo.color, "#1F2937");
  assert.equal(shape().onMap, true);
});

test("setStyle com valores adulterados cai no padrão sem quebrar", () => {
  const { view, shape } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  view.setStyle({ estilo: "<x>", cor_contorno: "red;x", cor_preenchimento: "url(javascript:1)", alfa_preenchimento: "2" });
  assert.deepEqual(shape().styles.at(-1), { color: "#C80000", fillColor: "#FFC800", fillOpacity: 89 / 255, weight: 3, opacity: 1 });
  assert.doesNotThrow(() => view.setStyle(null));
});

test("setCatalogo reaplica o estilo com a espessura do catálogo", () => {
  const { view, shape } = setup({ catalogo: CATALOGO_VAZIO });
  view.setStyle(PRANCHA_TECNICO);
  view.show(GEOMETRY, BOUNDS);
  assert.equal(shape().options.style.weight, 3); // sem catálogo: espessura do padrão
  view.setCatalogo(catalogo());
  assert.equal(shape().styles.at(-1).weight, 2);
});

test("reset volta ao estilo padrão", () => {
  const { view, shape } = setup({ catalogo: catalogo() });
  view.setStyle(PRANCHA_TECNICO);
  view.show(GEOMETRY, BOUNDS);
  view.reset();
  view.show(GEOMETRY, BOUNDS);
  assert.deepEqual(semClasse(shape().options.style), semClasse({ color: "#C80000", fillColor: "#FFC800", fillOpacity: 89 / 255, weight: 3, opacity: 1 }));
  assert.equal(view.state.view.base, "ruas_osm");
});

test("tileerror com catálogo marca tilesFailed e mantém o polígono", () => {
  const { view, tilesOn, shape } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  tilesOn()[0].fire("tileerror");
  assert.equal(view.state.tilesFailed, true);
  assert.equal(shape().onMap, true);
});

test("catálogo que chega depois da geometria liga o mapa-base inicial dele", () => {
  const { view, tilesOn } = setup({ catalogo: CATALOGO_VAZIO });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(tilesOn().length, 0);
  view.setCatalogo(catalogo());
  assert.equal(view.state.view.base, "ruas_osm");
  assert.equal(tilesOn().length, 1);
});

test("catálogo novo respeita o mapa-base escolhido pelo usuário; reset volta a seguir o catálogo", () => {
  const { view, tilesOn } = setup({ catalogo: catalogo() });
  view.show(GEOMETRY, BOUNDS);
  view.setBase("nenhum");
  view.setCatalogo(catalogo());
  assert.equal(view.state.view.base, "nenhum");
  assert.equal(tilesOn().length, 0);
  view.reset();
  view.setCatalogo(CATALOGO_VAZIO);
  view.setCatalogo(catalogo());
  assert.equal(view.state.view.base, "ruas_osm");
});

test("sem a opção catalogo o mapa começa sem catálogo: nenhum tile até setCatalogo", () => {
  const { L, log } = fakeLeaflet();
  const view = createMapView({ hidden: true }, { leaflet: L });
  view.show(GEOMETRY, BOUNDS);
  assert.equal(log.tiles.length, 0);
  assert.equal(view.state.view.base, "nenhum");
});

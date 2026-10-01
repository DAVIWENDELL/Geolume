import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { formatMetrics, parseGeoJsonPreview, parseMetrics } from "../../worker/web/js/geo.js";

const fixture = (name) => readFileSync(new URL(`../../worker/tests/fixtures/${name}`, import.meta.url), "utf8");

const square = [
  [-47.9, -15.8],
  [-47.9, -15.799],
  [-47.899, -15.799],
  [-47.899, -15.8],
  [-47.9, -15.8],
];
const feature = (geometry, extra = {}) => ({ type: "Feature", properties: {}, geometry, ...extra });
const collection = (...features) => ({ type: "FeatureCollection", features });
const parse = (obj) => parseGeoJsonPreview(JSON.stringify(obj));

function assertRejected(result, code) {
  assert.equal(result.ok, false);
  assert.equal(result.code, code);
  assert.equal(typeof result.message, "string");
  assert.ok(result.message.length > 0);
}

// ---- Válidos -------------------------------------------------------------

test("fixture gleba_rural_exemplo: polígono válido com bounds lat/lon", () => {
  const result = parseGeoJsonPreview(fixture("gleba_rural_exemplo.geojson"));
  assert.equal(result.ok, true);
  assert.equal(result.geometry.type, "Polygon");
  const [[south, west], [north, east]] = result.bounds;
  assert.ok(south < north && west < east);
  assert.ok(south > -16 && north < -15.7, "latitudes de Brasília");
  assert.ok(west > -48 && east < -47.9, "longitudes de Brasília");
});

test("fixtures validas no worker tambem passam na previa", () => {
  for (const name of ["lote_simples.geojson", "lote_boa_vista.geojson", "lote_minusculo.geojson"]) {
    assert.equal(parseGeoJsonPreview(fixture(name)).ok, true, name);
  }
});

test("bounds exatos do quadrado", () => {
  const result = parse(collection(feature({ type: "Polygon", coordinates: [square] })));
  assert.deepEqual(result.bounds, [
    [-15.8, -47.9],
    [-15.799, -47.899],
  ]);
});

test("aceita Feature solta e Geometry solta", () => {
  assert.equal(parse(feature({ type: "Polygon", coordinates: [square] })).ok, true);
  assert.equal(parse({ type: "Polygon", coordinates: [square] }).ok, true);
});

test("MultiPolygon de uma parte vira Polygon", () => {
  const result = parse(collection(feature({ type: "MultiPolygon", coordinates: [[square]] })));
  assert.equal(result.ok, true);
  assert.equal(result.geometry.type, "Polygon");
  assert.deepEqual(result.geometry.coordinates, [square]);
});

test("CRS declarado 4326, 4674 ou CRS84 e aceito", () => {
  for (const name of ["EPSG:4326", "EPSG:4674", "urn:ogc:def:crs:EPSG::4674", "urn:ogc:def:crs:OGC:1.3:CRS84"]) {
    const obj = collection(feature({ type: "Polygon", coordinates: [square] }));
    obj.crs = { type: "name", properties: { name } };
    assert.equal(parse(obj).ok, true, name);
  }
});

test("geometria devolvida contem so type e coordinates, sem propriedades", () => {
  const result = parse(collection(feature({ type: "Polygon", coordinates: [square] }, { properties: { nome: "<b>x</b>" } })));
  assert.deepEqual(Object.keys(result.geometry).sort(), ["coordinates", "type"]);
});

// ---- Inválidos -----------------------------------------------------------

test("JSON invalido", () => {
  assertRejected(parseGeoJsonPreview(fixture("invalido.json")), "json_invalido");
  assertRejected(parseGeoJsonPreview(""), "json_invalido");
  assertRejected(parseGeoJsonPreview("   "), "json_invalido");
});

test("JSON que nao e GeoJSON", () => {
  for (const value of [null, 42, "texto", [], { foo: 1 }, { type: "Qualquer" }]) {
    assertRejected(parseGeoJsonPreview(JSON.stringify(value)), "json_invalido");
  }
});

test("FeatureCollection vazia", () => {
  assertRejected(parseGeoJsonPreview(fixture("vazio.geojson")), "sem_feicoes");
  assertRejected(parse({ type: "FeatureCollection" }), "sem_feicoes");
});

test("multiplas feicoes", () => {
  assertRejected(parseGeoJsonPreview(fixture("multiplas_feicoes.geojson")), "multiplas_feicoes");
});

test("linha, ponto, geometria nula e GeometryCollection nao sao poligonos", () => {
  assertRejected(parseGeoJsonPreview(fixture("linha.geojson")), "geometria_nao_poligonal");
  assertRejected(parse(collection(feature({ type: "Point", coordinates: [-47.9, -15.8] }))), "geometria_nao_poligonal");
  assertRejected(parse(collection(feature(null))), "geometria_nao_poligonal");
  assertRejected(parse({ type: "GeometryCollection", geometries: [] }), "geometria_nao_poligonal");
  assertRejected(parse({ type: "MultiPolygon", coordinates: [] }), "geometria_nao_poligonal");
});

test("MultiPolygon com varias partes", () => {
  const other = square.map(([x, y]) => [x + 0.01, y]);
  assertRejected(parse({ type: "MultiPolygon", coordinates: [[square], [other]] }), "multiplas_partes");
});

test("poligono com furo", () => {
  assertRejected(parseGeoJsonPreview(fixture("com_furo.geojson")), "poligono_com_furos");
});

test("autointersecao", () => {
  assertRejected(parseGeoJsonPreview(fixture("autointersecao.geojson")), "geometria_invalida");
});

test("anel que toca a si mesmo em um vertice e invalido", () => {
  // Gravata com vértice compartilhado no meio: (0,0) aparece duas vezes fora das pontas.
  const ring = [
    [-47.9, -15.8],
    [-47.899, -15.8],
    [-47.8995, -15.7995],
    [-47.899, -15.799],
    [-47.9, -15.799],
    [-47.8995, -15.7995],
    [-47.9, -15.8],
  ];
  assertRejected(parse({ type: "Polygon", coordinates: [ring] }), "geometria_invalida");
});

test("anel aberto, curto ou sem area", () => {
  assertRejected(parse({ type: "Polygon", coordinates: [square.slice(0, 4)] }), "geometria_invalida");
  assertRejected(parse({ type: "Polygon", coordinates: [[square[0], square[1], square[0]]] }), "geometria_invalida");
  const flat = [
    [-47.9, -15.8],
    [-47.899, -15.8],
    [-47.898, -15.8],
    [-47.9, -15.8],
  ];
  assertRejected(parse({ type: "Polygon", coordinates: [flat] }), "geometria_invalida");
  assertRejected(parse({ type: "Polygon", coordinates: [] }), "geometria_invalida");
});

test("coordenadas nao numericas", () => {
  const ring = square.map((p) => [...p]);
  ring[1] = ["a", -15.799];
  assertRejected(parse({ type: "Polygon", coordinates: [ring] }), "coordenadas_invalidas");
  assertRejected(parse({ type: "Polygon", coordinates: [[1, 2, 3, 4]] }), "coordenadas_invalidas");
});

test("CRS UTM declarado e rejeitado", () => {
  assertRejected(parseGeoJsonPreview(fixture("crs_utm.geojson")), "crs_nao_suportado");
});

test("coordenadas fora de latitude/longitude sem CRS declarado", () => {
  const utm = [
    [190000, 8250000],
    [190000, 8250100],
    [190100, 8250100],
    [190100, 8250000],
    [190000, 8250000],
  ];
  assertRejected(parse({ type: "Polygon", coordinates: [utm] }), "coordenadas_invalidas");
});

test("fora da cobertura SIRGAS 2000 / UTM", () => {
  assertRejected(parseGeoJsonPreview(fixture("europa.geojson")), "fora_da_cobertura");
});

test("excesso de vertices", () => {
  const n = 5001;
  const ring = [];
  for (let i = 0; i < n; i++) {
    const a = (2 * Math.PI * i) / n;
    ring.push([-47.9 + 0.01 * Math.cos(a), -15.8 + 0.01 * Math.sin(a)]);
  }
  ring.push(ring[0]);
  assertRejected(parse({ type: "Polygon", coordinates: [ring] }), "excesso_de_vertices");
});

test("5000 vertices validos processam rapido", () => {
  const n = 5000;
  const ring = [];
  for (let i = 0; i < n; i++) {
    const a = (2 * Math.PI * i) / n;
    ring.push([-47.9 + 0.01 * Math.cos(a), -15.8 + 0.01 * Math.sin(a)]);
  }
  ring.push(ring[0]);
  const start = performance.now();
  assert.equal(parse({ type: "Polygon", coordinates: [ring] }).ok, true);
  assert.ok(performance.now() - start < 1500, "validação de 5000 vértices abaixo de 1,5 s");
});

// ---- Métricas do resultado.json -------------------------------------------

test("parseMetrics le area e perimetro", () => {
  assert.deepEqual(parseMetrics({ area_ha: 1.1875, perimetro_m: 435.94, outros: 1 }), {
    areaHa: 1.1875,
    perimetroM: 435.94,
  });
});

test("parseMetrics recusa dados ausentes ou malformados", () => {
  for (const value of [null, undefined, "x", {}, { area_ha: 1 }, { area_ha: "1", perimetro_m: 2 }, { area_ha: NaN, perimetro_m: 2 }, { area_ha: -1, perimetro_m: 2 }]) {
    assert.equal(parseMetrics(value), null, JSON.stringify(value));
  }
});

test("formatMetrics em pt-BR", () => {
  assert.deepEqual(formatMetrics({ areaHa: 1.1875, perimetroM: 435.94 }), {
    area: "1,1875 ha",
    perimetro: "435,94 m",
  });
  assert.deepEqual(formatMetrics({ areaHa: 12345.5, perimetroM: 12000 }), {
    area: "12.345,5 ha",
    perimetro: "12.000 m",
  });
});

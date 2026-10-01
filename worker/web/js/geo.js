// Leitura do GeoJSON para a pré-visualização: sem DOM, sem rede.
// Espelha as regras de worker/geolume_worker/input_loader.py; o worker continua
// sendo a fonte da verdade, isto é só um aviso antecipado.

export const MAX_VERTICES = 5000;
const CRS_ACEITOS = new Set(["4326", "4674", "CRS84"]);

const MESSAGES = {
  json_invalido: "O arquivo não é um GeoJSON válido.",
  sem_feicoes: "O GeoJSON não contém feições.",
  multiplas_feicoes: "O arquivo tem mais de uma feição; envie um polígono por arquivo.",
  geometria_nao_poligonal: "A feição precisa ser um polígono.",
  multiplas_partes: "O polígono tem mais de uma parte.",
  poligono_com_furos: "Polígonos com furos não são suportados.",
  crs_nao_suportado: "O sistema de coordenadas não é suportado; use EPSG:4326 ou EPSG:4674.",
  coordenadas_invalidas: "As coordenadas não são latitude/longitude válidas.",
  geometria_invalida: "O polígono é inválido (anel aberto, sem área ou com autointerseção).",
  excesso_de_vertices: `O polígono excede ${MAX_VERTICES} vértices.`,
  fora_da_cobertura: "A área está fora dos fusos SIRGAS 2000 / UTM suportados.",
};

class PreviewError extends Error {
  constructor(code) {
    super(MESSAGES[code]);
    this.code = code;
  }
}

const fail = (code) => {
  throw new PreviewError(code);
};

function singleGeometry(data) {
  if (data === null || typeof data !== "object" || Array.isArray(data)) fail("json_invalido");
  if (data.type === "FeatureCollection") {
    const features = Array.isArray(data.features) ? data.features : [];
    if (features.length === 0) fail("sem_feicoes");
    if (features.length > 1) fail("multiplas_feicoes");
    return features[0]?.geometry ?? null;
  }
  if (data.type === "Feature") return data.geometry ?? null;
  if (["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"].includes(data.type)) {
    return data;
  }
  return fail("json_invalido");
}

function polygonRings(geometry) {
  if (!geometry || !Array.isArray(geometry.coordinates)) fail("geometria_nao_poligonal");
  if (geometry.type === "MultiPolygon") {
    if (geometry.coordinates.length === 0) fail("geometria_nao_poligonal");
    if (geometry.coordinates.length > 1) fail("multiplas_partes");
    return geometry.coordinates[0];
  }
  if (geometry.type !== "Polygon") fail("geometria_nao_poligonal");
  return geometry.coordinates;
}

function checkCrs(data) {
  const name = data?.crs?.properties?.name;
  if (typeof name !== "string") return; // sem CRS declarado: GeoJSON é WGS 84
  const code = /CRS84$/i.test(name) ? "CRS84" : name.match(/EPSG:+(\d+)$/i)?.[1];
  if (!CRS_ACEITOS.has(code)) fail("crs_nao_suportado");
}

function readRing(ring) {
  if (!Array.isArray(ring)) fail("geometria_invalida");
  return ring.map((p) => {
    if (!Array.isArray(p) || p.length < 2 || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) {
      fail("coordenadas_invalidas");
    }
    const [lon, lat] = p;
    if (lon < -180 || lon > 180 || lat < -90 || lat > 90) fail("coordenadas_invalidas");
    return [lon, lat];
  });
}

const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
const sign = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
const within = (p, a, b) =>
  Math.min(a[0], b[0]) <= p[0] && p[0] <= Math.max(a[0], b[0]) && Math.min(a[1], b[1]) <= p[1] && p[1] <= Math.max(a[1], b[1]);

function segmentsTouch(a, b, c, d) {
  const d1 = sign(cross(a, b, c));
  const d2 = sign(cross(a, b, d));
  const d3 = sign(cross(c, d, a));
  const d4 = sign(cross(c, d, b));
  if (d1 !== d2 && d3 !== d4 && d1 !== 0 && d2 !== 0 && d3 !== 0 && d4 !== 0) return true;
  return (d1 === 0 && within(c, a, b)) || (d2 === 0 && within(d, a, b)) || (d3 === 0 && within(a, c, d)) || (d4 === 0 && within(b, c, d));
}

/** Anel fechado, com área e sem autointerseção (inclui toque em vértice e ida-e-volta). */
function isSimpleRing(ring) {
  if (ring.length < 4) return false;
  const first = ring[0];
  const last = ring[ring.length - 1];
  if (first[0] !== last[0] || first[1] !== last[1]) return false;

  const pts = ring.filter((p, i) => i === 0 || p[0] !== ring[i - 1][0] || p[1] !== ring[i - 1][1]);
  const n = pts.length - 1; // segmentos; pts[n] === pts[0]
  if (n < 3) return false;

  let area = 0;
  for (let i = 0; i < n; i++) area += cross([0, 0], pts[i], pts[i + 1]);
  if (area === 0) return false;

  for (let i = 0; i < n; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    const c = pts[(i + 2) % n];
    // Segmentos vizinhos colineares voltando sobre si mesmos.
    if (cross(a, b, c) === 0 && (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]) < 0) return false;
  }

  const boxes = [];
  for (let i = 0; i < n; i++) {
    const [a, b] = [pts[i], pts[i + 1]];
    boxes.push([Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]);
  }
  for (let i = 0; i < n; i++) {
    const bi = boxes[i];
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue; // primeiro e último segmento compartilham o fechamento
      const bj = boxes[j];
      if (bj[0] > bi[2] || bj[2] < bi[0] || bj[1] > bi[3] || bj[3] < bi[1]) continue;
      if (segmentsTouch(pts[i], pts[i + 1], pts[j], pts[j + 1])) return false;
    }
  }
  return true;
}

function centroid(ring) {
  let a2 = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [x0, y0] = ring[i];
    const [x1, y1] = ring[i + 1];
    const f = x0 * y1 - x1 * y0;
    a2 += f;
    cx += (x0 + x1) * f;
    cy += (y0 + y1) * f;
  }
  return [cx / (3 * a2), cy / (3 * a2)];
}

/** Mesma regra de geometry.utm_epsg_for: fusos 18..25 no sul, 17..22 no norte. */
function inUtmCoverage([lon, lat]) {
  const fuso = Math.floor((lon + 180) / 6) + 1;
  return lat < 0 ? fuso >= 18 && fuso <= 25 : fuso >= 17 && fuso <= 22;
}

function bounds(ring) {
  let [south, west, north, east] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [lon, lat] of ring) {
    south = Math.min(south, lat);
    north = Math.max(north, lat);
    west = Math.min(west, lon);
    east = Math.max(east, lon);
  }
  return [
    [south, west],
    [north, east],
  ];
}

/**
 * Valida o texto de um GeoJSON com um único polígono em lat/lon.
 * Retorna { ok: true, geometry: { type: "Polygon", coordinates }, bounds: [[s, w], [n, e]] }
 * ou { ok: false, code, message }.
 */
export function parseGeoJsonPreview(text) {
  try {
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      fail("json_invalido");
    }
    const rings = polygonRings(singleGeometry(data));
    if (rings.length === 0) fail("geometria_invalida");
    if (rings.length > 1) fail("poligono_com_furos");
    checkCrs(data);
    const ring = readRing(rings[0]);
    if (ring.length - 1 > MAX_VERTICES) fail("excesso_de_vertices");
    if (!isSimpleRing(ring)) fail("geometria_invalida");
    if (!inUtmCoverage(centroid(ring))) fail("fora_da_cobertura");
    return { ok: true, geometry: { type: "Polygon", coordinates: [ring] }, bounds: bounds(ring) };
  } catch (err) {
    if (err instanceof PreviewError) return { ok: false, code: err.code, message: err.message };
    throw err;
  }
}

/** Área e perímetro do resultado.json; null se ausentes ou malformados. */
export function parseMetrics(data) {
  const area = data?.area_ha;
  const perimetro = data?.perimetro_m;
  if (!Number.isFinite(area) || !Number.isFinite(perimetro) || area < 0 || perimetro < 0) return null;
  return { areaHa: area, perimetroM: perimetro };
}

export function formatMetrics({ areaHa, perimetroM }) {
  const fmt = (value, digits) => value.toLocaleString("pt-BR", { maximumFractionDigits: digits });
  return { area: `${fmt(areaHa, 4)} ha`, perimetro: `${fmt(perimetroM, 2)} m` };
}

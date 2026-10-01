// Camadas do mapa: mapas-base disponíveis, escolhas padrão e legenda. Sem DOM nem Leaflet.

import { PRANCHA_PADRAO, validateAlfa } from "./prancha.js";

// Constante do código: é o único HTML que o Leaflet insere na página.
const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">Contribuidores do OpenStreetMap</a>';

// Satélite fica desabilitado e sem URL até haver provedor com licença comercial confirmada.
export const BASEMAPS = Object.freeze([
  Object.freeze({
    id: "ruas",
    label: "Mapa de ruas (OpenStreetMap)",
    enabled: true,
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    maxZoom: 19,
    attribution: OSM_ATTRIBUTION,
  }),
  Object.freeze({ id: "nenhum", label: "Sem mapa-base", enabled: true, url: null }),
  Object.freeze({ id: "satelite", label: "Satélite — em breve", enabled: false, url: null }),
]);

// 35%: o mesmo alfa (90/255) do preenchimento no mapa.pdf.
export const DEFAULT_VIEW = Object.freeze({ base: "ruas", polygon: true, opacity: 0.35 });

export function findBasemap(id) {
  return BASEMAPS.find((basemap) => basemap.id === id) ?? null;
}

/** Nova escolha de mapa-base; camada desconhecida ou desabilitada mantém a atual.
 *  Com catálogo, só um mapa-base ativável dele (a forma sem catálogo sai com BASEMAPS). */
export function withBase(view, id, catalogo) {
  if (catalogo) {
    const camada = Array.isArray(catalogo.camadas) ? catalogo.camadas.find((c) => c.id === id && c.grupo === "base") : null;
    return camadaAtivavel(camada) ? { ...view, base: camada.id } : view;
  }
  const basemap = findBasemap(id);
  return basemap?.enabled ? { ...view, base: basemap.id } : view;
}

/** Valor do controle (0–100) como opacidade do preenchimento (0–1). */
export function opacityFromPercent(value) {
  const percent = Number(value);
  if (String(value).trim() === "" || !Number.isFinite(percent)) return DEFAULT_VIEW.opacity;
  return Math.min(100, Math.max(0, Math.round(percent))) / 100;
}

export function percentFromOpacity(opacity) {
  return Math.round(opacity * 100);
}

// ---- Catálogo servido pela API (GET /camadas) ------------------------------------------
// O navegador não tem lista própria: só confere o que chegou e descarta o que não for seguro.

const ID = /^[a-z0-9_]{1,32}$/;
const COR = /^#[0-9A-Fa-f]{6}$/;
const SITUACOES = ["disponivel", "depende_fonte_oficial", "planejada"];
const texto = (valor) => typeof valor === "string";
const https = (url) => texto(url) && url.startsWith("https://");

function camadaValida(camada) {
  if (!camada || typeof camada !== "object" || !ID.test(camada.id) || !texto(camada.nome)) return false;
  if (!SITUACOES.includes(camada.situacao) || !Number.isFinite(camada.ordem)) return false;
  // URL só em camada disponível, e só https: nada de http:, javascript:, data: ou //host.
  if (camada.url !== null && camada.url !== undefined) return camada.situacao === "disponivel" && https(camada.url);
  return true;
}

function estiloValido(estilo) {
  return Boolean(estilo) && ID.test(estilo.id) && texto(estilo.nome) && COR.test(estilo.contorno)
    && COR.test(estilo.preenchimento) && Number.isInteger(estilo.espessura_px) && estilo.espessura_px > 0;
}

/** Corpo de GET /camadas ⇒ { camadas, estilos, alfaPadrao }, ou null se não for um catálogo. */
export function parseCatalogo(body) {
  if (!body || typeof body !== "object" || !Array.isArray(body.camadas) || !Array.isArray(body.estilos)) return null;
  const alfa = validateAlfa(body.alfa_padrao);
  if (typeof body.alfa_padrao !== "number" || !alfa.ok) return null;
  return {
    camadas: body.camadas.filter(camadaValida),
    estilos: body.estilos.filter(estiloValido),
    alfaPadrao: alfa.value,
  };
}

/** Usado quando o catálogo não carrega: polígono sem mapa-base, nenhuma requisição de tiles. */
export const CATALOGO_VAZIO = Object.freeze({
  camadas: Object.freeze([
    Object.freeze({ id: "nenhum", nome: "Sem mapa-base", tipo: "nenhum", grupo: "base", situacao: "disponivel",
      ordem: 0, fonte: null, url: null, max_zoom: null, exporta_pdf: false, visivel: true, aviso: null }),
    Object.freeze({ id: "poligono", nome: "Polígono do imóvel", tipo: "vetor", grupo: "sobreposicao",
      situacao: "disponivel", ordem: 100, fonte: null, url: null, max_zoom: null, exporta_pdf: true, visivel: true,
      aviso: null }),
  ]),
  estilos: Object.freeze([]),
  alfaPadrao: PRANCHA_PADRAO.alfa_preenchimento,
});

// Transitório: BASEMAPS no formato do catálogo, para o mapa funcionar até a interface buscar
// GET /camadas (Task 7). Mesmos ids ("ruas", "nenhum", "satelite") que o painel atual usa.
const OSM_ATRIBUICAO = Object.freeze({ texto: "© Contribuidores do OpenStreetMap", url: "https://www.openstreetmap.org/copyright" });
export const CATALOGO_LEGADO = Object.freeze({
  camadas: Object.freeze([
    Object.freeze({ id: "ruas", nome: BASEMAPS[0].label, tipo: "raster", grupo: "base", situacao: "disponivel", ordem: 0,
      fonte: Object.freeze({ nome: "OpenStreetMap", atribuicao: OSM_ATRIBUICAO }), url: BASEMAPS[0].url,
      max_zoom: BASEMAPS[0].maxZoom, exporta_pdf: false, visivel: true, aviso: "Somente na pré-visualização — não entra no PDF" }),
    CATALOGO_VAZIO.camadas[0],
    Object.freeze({ id: "satelite", nome: BASEMAPS[2].label, tipo: "raster", grupo: "base",
      situacao: "depende_fonte_oficial", ordem: 0, fonte: null, url: null, max_zoom: null, exporta_pdf: false, visivel: false,
      aviso: "Sem provedor licenciado" }),
    CATALOGO_VAZIO.camadas[1],
  ]),
  estilos: Object.freeze([]),
  alfaPadrao: PRANCHA_PADRAO.alfa_preenchimento,
});

const porOrdem = (camadas) => [...camadas].sort((a, b) => a.ordem - b.ordem);

/** Camadas do painel por situação, cada grupo em ordem de desenho. */
export function grupos(catalogo) {
  const de = (situacao) => porOrdem(catalogo.camadas.filter((camada) => camada.situacao === situacao));
  return { disponiveis: de("disponivel"), dependemFonte: de("depende_fonte_oficial"), planejadas: de("planejada") };
}

/** Opções de mapa-base (escolha única), inclusive as ainda não ativáveis. */
export function basemaps(catalogo) {
  return catalogo.camadas.filter((camada) => camada.grupo === "base");
}

export function camadaAtivavel(camada) {
  if (!camada || camada.situacao !== "disponivel") return false;
  return camada.tipo === "raster" ? https(camada.url) : true;
}

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapar = (valor) => String(valor).replace(/[&<>"']/g, (c) => ESCAPES[c]);

/** Atribuição como HTML (o Leaflet a insere como HTML): tudo escapado; link só para https. */
export function atribuicaoHtml(atribuicao) {
  if (!atribuicao || !texto(atribuicao.texto)) return "";
  const rotulo = escapar(atribuicao.texto);
  if (!https(atribuicao.url)) return rotulo;
  return `<a href="${escapar(atribuicao.url)}" rel="noopener noreferrer" target="_blank">${rotulo}</a>`;
}

/** Itens da legenda das camadas ativas, em texto puro.
 *  legendItems(view, catalogo, { tilesFailed }); sem catálogo, a forma antiga (view, { tilesFailed })
 *  continua valendo até map.js/render.js passarem a usar o catálogo. */
export function legendItems(view, catalogo, opcoes) {
  if (!Array.isArray(catalogo?.camadas)) return legendItemsBasemaps(view, catalogo);
  const { tilesFailed = false } = opcoes ?? {};
  const camada = catalogo.camadas.find((c) => c.id === view.base && c.grupo === "base");
  const ativa = camadaAtivavel(camada) && camada.tipo === "raster";
  const items = [{ kind: "base", text: ativa ? `Mapa-base: ${camada.nome}${tilesFailed ? " — indisponível" : ""}` : "Mapa-base: nenhum" }];
  if (ativa && texto(camada.aviso) && camada.aviso) items.push({ kind: "aviso", text: camada.aviso });
  if (view.polygon) items.push({ kind: "poligono", text: "Polígono do imóvel" });
  return items;
}

function legendItemsBasemaps(view, { tilesFailed = false } = {}) {
  const basemap = findBasemap(view.base);
  const base = basemap?.url ? `Mapa-base: ${basemap.label}${tilesFailed ? " — indisponível" : ""}` : "Mapa-base: nenhum";
  const items = [{ kind: "base", text: base }];
  if (view.polygon) {
    items.push({ kind: "poligono", text: `Polígono do imóvel (preenchimento ${percentFromOpacity(view.opacity)}%)` });
  }
  return items;
}

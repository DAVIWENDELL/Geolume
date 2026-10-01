// Camadas do mapa a partir do catálogo de GET /camadas: painel, mapa-base e legenda. Sem DOM nem Leaflet.

import { PRANCHA_PADRAO, validateAlfa } from "./prancha.js";

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

const baseDoCatalogo = (catalogo, id) =>
  (Array.isArray(catalogo?.camadas) ? catalogo.camadas.find((c) => c.id === id && c.grupo === "base") : null) ?? null;

/** Nova escolha de mapa-base: só um mapa-base ativável do catálogo; senão (inclusive sem catálogo) mantém a atual. */
export function withBase(view, id, catalogo) {
  const camada = baseDoCatalogo(catalogo, id);
  return camadaAtivavel(camada) ? { ...view, base: camada.id } : view;
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

/** Itens da legenda das camadas ativas, em texto puro; sem catálogo, nenhum mapa-base. */
export function legendItems(view, catalogo, { tilesFailed = false } = {}) {
  const camada = baseDoCatalogo(catalogo, view.base);
  const ativa = camadaAtivavel(camada) && camada.tipo === "raster";
  const items = [{ kind: "base", text: ativa ? `Mapa-base: ${camada.nome}${tilesFailed ? " — indisponível" : ""}` : "Mapa-base: nenhum" }];
  if (ativa && texto(camada.aviso) && camada.aviso) items.push({ kind: "aviso", text: camada.aviso });
  if (view.polygon) items.push({ kind: "poligono", text: "Polígono do imóvel" });
  return items;
}

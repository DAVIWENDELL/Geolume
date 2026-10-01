// Mapa da pré-visualização: única camada que conhece o Leaflet (global `L`).
// Criado só na primeira geometria, para não baixar tiles à toa. O polígono não depende
// dos tiles: sem mapa-base ou com os tiles falhando, a geometria continua desenhada.
// Camadas e estilos vêm só do catálogo (GET /camadas); sem catálogo, nenhum tile é pedido.
import { CATALOGO_VAZIO, atribuicaoHtml, camadaAtivavel, withBase } from "./layers.js";
import { PRANCHA_PADRAO, polygonStyle, validateAlfa } from "./prancha.js";

// Mapa-base e polígono em panes próprias: a ordem do catálogo decide quem fica por cima,
// e o mapa-base nunca passa do polígono (tilePane do Leaflet = 200, overlayPane = 400).
const PANE_BASE = "geolume-base";
const PANE_POLIGONO = "geolume-poligono";
const Z_BASE = 200;
const Z_POLIGONO = 400;
const CLASSE = "geolume-poligono";

const catalogoOuVazio = (catalogo) => (Array.isArray(catalogo?.camadas) ? catalogo : CATALOGO_VAZIO);
const ordemDe = (camada) => (Number.isFinite(camada?.ordem) ? camada.ordem : 0);

/** Mapa-base inicial: o primeiro raster ativável na ordem do catálogo; senão, o primeiro ativável. */
function baseInicial(catalogo) {
  const bases = catalogo.camadas.filter((c) => c.grupo === "base" && camadaAtivavel(c));
  const ordenadas = [...bases].sort((a, b) => ordemDe(a) - ordemDe(b));
  return (ordenadas.find((c) => c.tipo === "raster") ?? ordenadas[0])?.id ?? null;
}

/**
 * onChange({ visible, view, tilesFailed }) roda a cada mudança, para o painel e a legenda.
 * view = { base, polygon, opacity } vale para todas as geometrias até o reset; opacity é o
 * alfa da prancha (0–1). catalogo: o de parseCatalogo; até ele chegar (setCatalogo), nenhum tile.
 */
export function createMapView(container, { leaflet, onChange = () => {}, catalogo = CATALOGO_VAZIO } = {}) {
  const lib = () => leaflet ?? globalThis.L;
  let cat = catalogoOuVazio(catalogo);
  let map = null;
  let tiles = null;
  let layer = null;
  let bounds = null;
  let prancha = { ...PRANCHA_PADRAO };
  const viewPadrao = () => ({ base: baseInicial(cat), polygon: true, opacity: PRANCHA_PADRAO.alfa_preenchimento });
  let view = viewPadrao();
  let baseEscolhida = false; // o usuário trocou o mapa-base: catálogo novo não passa por cima
  let tilesFailed = false;

  const state = () => ({ visible: !container.hidden, view: { ...view }, tilesFailed });
  const changed = () => onChange(state());
  const estilo = () => polygonStyle(prancha, cat.estilos);
  const camadaBase = () => cat.camadas.find((c) => c.id === view.base && c.grupo === "base") ?? null;

  function pane(name, zIndex) {
    const element = map.getPane(name) ?? map.createPane(name);
    element.style.zIndex = String(zIndex);
  }

  function applyBase() {
    tiles?.remove();
    tiles = null;
    tilesFailed = false;
    const camada = camadaBase();
    // Só raster disponível com https; o resto (inclusive "nenhum") não pede nada à rede.
    if (!map || !camadaAtivavel(camada) || camada.tipo !== "raster") return;
    try {
      pane(PANE_BASE, Math.min(Z_BASE + ordemDe(camada), Z_POLIGONO - 1));
      const current = lib().tileLayer(camada.url, {
        pane: PANE_BASE,
        maxZoom: camada.max_zoom ?? undefined,
        attribution: atribuicaoHtml(camada.fonte?.atribuicao),
      });
      current.on("tileerror", () => {
        if (tiles !== current || tilesFailed) return; // erro de uma camada já trocada
        tilesFailed = true;
        changed();
      });
      tiles = current.addTo(map);
    } catch {
      tiles = null;
      tilesFailed = true; // mesmo aviso de tiles indisponíveis; o polígono segue
    }
  }

  /** Descarta mapa e camadas (inclusive um mapa criado pela metade); as escolhas ficam. */
  function discard() {
    try {
      layer?.remove();
      tiles?.remove();
      map?.remove();
    } catch {
      // Leaflet em estado inconsistente: basta soltar as referências.
    }
    map = tiles = layer = bounds = null;
    tilesFailed = false;
    container.hidden = true;
  }

  function applyPolygon() {
    if (!layer) return;
    if (view.polygon) layer.addTo(map);
    else layer.remove();
  }

  /** Cores, espessura e alfa juntos, numa chamada só. */
  function applyStyle() {
    layer?.setStyle(estilo());
  }

  return {
    get available() {
      return Boolean(lib());
    },

    get state() {
      return state();
    },

    /** Desenha só a geometria (sem propriedades) e enquadra; false se o Leaflet não carregou ou falhou. */
    show(geometry, nextBounds) {
      const L = lib();
      if (!L) return false;
      try {
        container.hidden = false;
        if (!map) {
          map = L.map(container, { scrollWheelZoom: false });
          const poligono = cat.camadas.find((c) => c.id === "poligono");
          pane(PANE_POLIGONO, Z_POLIGONO + Math.max(0, ordemDe(poligono)));
          applyBase();
        }
        layer?.remove();
        layer = L.geoJSON(geometry, { pane: PANE_POLIGONO, style: { className: CLASSE, ...estilo() }, interactive: false });
        bounds = nextBounds;
        applyPolygon();
        map.invalidateSize();
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 18 });
      } catch {
        discard(); // a tela segue sem mapa, como se o Leaflet não tivesse carregado
        changed();
        return false;
      }
      changed();
      return true;
    },

    /** Catálogo novo (ou CATALOGO_VAZIO quando GET /camadas falha): mapa-base e estilo refeitos. */
    setCatalogo(next) {
      cat = catalogoOuVazio(next);
      if (!baseEscolhida || !camadaAtivavel(camadaBase())) view = { ...view, base: baseInicial(cat) };
      applyBase();
      applyStyle();
      changed();
    },

    /** Troca o mapa-base; false para camada desconhecida ou não ativável (nada é pedido). */
    setBase(id) {
      const next = withBase(view, id, cat);
      if (next === view) return false;
      view = next;
      baseEscolhida = true;
      applyBase();
      changed();
      return true;
    },

    setPolygonVisible(visible) {
      view = { ...view, polygon: Boolean(visible) };
      applyPolygon();
      changed();
    },

    /** Prancha do job ({ estilo, cor_contorno, cor_preenchimento, alfa_preenchimento }); o que
     *  estiver fora da regra cai no padrão, e o contorno fica sempre opaco. */
    setStyle(next) {
      const alfa = validateAlfa(next?.alfa_preenchimento);
      prancha = { ...next };
      view = { ...view, opacity: alfa.ok ? alfa.value : PRANCHA_PADRAO.alfa_preenchimento };
      applyStyle();
      changed();
    },

    /** Enquadra a geometria atual (mesmo com o polígono oculto). */
    fit() {
      if (!map || !bounds || container.hidden) return false;
      try {
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 18 });
      } catch {
        return false;
      }
      return true;
    },

    clear() {
      layer?.remove();
      layer = null;
      bounds = null;
      container.hidden = true;
      changed();
    },

    /** Logout: escolhas e estilo voltam ao padrão e o mapa é descartado (recriado na próxima geometria). */
    reset() {
      discard();
      prancha = { ...PRANCHA_PADRAO };
      view = viewPadrao();
      baseEscolhida = false;
      changed();
    },
  };
}

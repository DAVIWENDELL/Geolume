// Personalização da prancha (mapa.pdf): regras espelhadas de geolume_worker/prancha.py.
// Sem DOM nem rede. A API revalida tudo; isto só evita um envio que ela recusaria.

export const TEXTO_MAX = 100;
export const LOGO_MAX_BYTES = 2 * 1024 * 1024;
const TITULO_PADRAO = "GeoLume — Mapa de Localização";
const LOGO_TIPOS = ["image/png", "image/jpeg"];
// Layouts prontos da prancha, na ordem dos cartões; valores = LEGENDAS de prancha.py.
export const LAYOUTS = Object.freeze(
  [
    { valor: "nenhuma", nome: "Padrão", descricao: "Mapa, dados do imóvel, norte, escala e tabela de vértices." },
    { valor: "lateral", nome: "Legenda lateral", descricao: "Legenda na coluna direita, abaixo da escala." },
    { valor: "inferior", nome: "Legenda inferior", descricao: "Legenda abaixo do mapa, ao lado da tabela." },
  ].map(Object.freeze),
);
const LEGENDAS = LAYOUTS.map((layout) => layout.valor);
const COR = /^#[0-9A-Fa-f]{6}$/;
// Controles (categoria Cc) e controles bidirecionais, recusados também no servidor.
const CONTROLE = /[\u0000-\u001f\u007f-\u009f‪-‮⁦-⁩]/;

export const PRANCHA_PADRAO = Object.freeze({
  projeto: "",
  responsavel: "",
  cor_contorno: "#C80000",
  cor_preenchimento: "#FFC800",
  legenda: "nenhuma",
  estilo: "padrao",
  alfa_preenchimento: 0.35,
});

// Alfa do preenchimento: mesmas regras de validar_alfa (camadas.py), conferidas pela tabela
// worker/tests/fixtures/alfa_casos.json. Só dígitos ASCII com ponto; só espaço ASCII é aparado.
const ALFA_TEXTO = /^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/;
const ESPACOS = /^[ \t\n\r\f\v]+|[ \t\n\r\f\v]+$/g;
const ALFA_INVALIDO = "Opacidade do preenchimento deve estar entre 0 e 1.";
const ESTILO_INVALIDO = "Estilo do polígono inválido.";
// Sem catálogo, o polígono ainda é desenhado: estilo padrão com a espessura dele (3 px).
const ESPESSURA_PADRAO_PX = 3;

const ROTULOS = { projeto: "Nome do projeto", responsavel: "Responsável técnico" };

export function pranchaTitle(projeto) {
  const nome = String(projeto ?? "").trim();
  return nome ? `${nome} — Mapa de Localização` : TITULO_PADRAO;
}

export function responsavelLine(responsavel) {
  const nome = String(responsavel ?? "").trim();
  return nome ? `Responsável técnico: ${nome}` : "";
}

const erro = (field, message) => ({ ok: false, field, message });

/** Alfa em [0, 1]; ausente ou vazio é o padrão. Inválido é recusado, nunca corrigido. */
export function validateAlfa(valor) {
  if (valor === null || valor === undefined) return { ok: true, value: PRANCHA_PADRAO.alfa_preenchimento };
  let numero;
  if (typeof valor === "string") {
    const texto = valor.replace(ESPACOS, "");
    if (texto === "") return { ok: true, value: PRANCHA_PADRAO.alfa_preenchimento };
    if (!ALFA_TEXTO.test(texto)) return { ok: false, message: ALFA_INVALIDO };
    numero = Number(texto);
  } else if (typeof valor === "number") {
    numero = valor;
  } else {
    return { ok: false, message: ALFA_INVALIDO };
  }
  if (!Number.isFinite(numero) || numero < 0 || numero > 1) return { ok: false, message: ALFA_INVALIDO };
  return { ok: true, value: numero };
}

/** Controle deslizante (inteiro 0–100) como alfa 0–1. */
export function alfaFromPercent(percent) {
  const texto = typeof percent === "number" ? String(percent) : typeof percent === "string" ? percent : "";
  if (!/^[0-9]{1,3}$/.test(texto) || Number(texto) > 100) return { ok: false, message: ALFA_INVALIDO };
  return { ok: true, value: Number(texto) / 100 };
}

/** Alfa em 0–255, igual ao do mapa.pdf; o Leaflet usa alfa8 / 255. */
export function alfa8(alfa) {
  return Math.floor(alfa * 255 + 0.5);
}

function estiloDoCatalogo(id, estilos) {
  return (Array.isArray(estilos) && estilos.find((estilo) => estilo.id === id)) || null;
}

/** Cores de um estilo do catálogo, para preencher os seletores; desconhecido ⇒ null. */
export function coresDoEstilo(id, estilos) {
  const estilo = typeof id === "string" ? estiloDoCatalogo(id, estilos) : null;
  return estilo ? { cor_contorno: estilo.contorno, cor_preenchimento: estilo.preenchimento } : null;
}

/** fields: valores do formulário; estilos: os do catálogo (sem catálogo, só o padrão).
 *  { ok, values } normalizado ou { ok: false, field, message }. */
export function validatePrancha(fields, estilos = []) {
  const values = {};
  for (const campo of ["projeto", "responsavel"]) {
    const texto = String(fields[campo] ?? "").trim();
    if (CONTROLE.test(texto)) {
      return erro(campo, `${ROTULOS[campo]} não pode ter quebras de linha nem caracteres de controle.`);
    }
    // Conta caracteres como o Python (pontos de código), não unidades UTF-16.
    if ([...texto].length > TEXTO_MAX) return erro(campo, `${ROTULOS[campo]} deve ter no máximo ${TEXTO_MAX} caracteres.`);
    values[campo] = texto;
  }
  for (const campo of ["cor_contorno", "cor_preenchimento"]) {
    const cor = String(fields[campo] ?? "") || PRANCHA_PADRAO[campo];
    if (!COR.test(cor)) return erro(campo, "Cor deve estar no formato #RRGGBB.");
    values[campo] = cor.toUpperCase();
  }
  const legenda = fields.legenda || PRANCHA_PADRAO.legenda;
  if (!LEGENDAS.includes(legenda)) return erro("legenda", "Layout da prancha inválido.");
  values.legenda = legenda;
  const estilo = fields.estilo || PRANCHA_PADRAO.estilo;
  if (estilo !== PRANCHA_PADRAO.estilo && !estiloDoCatalogo(estilo, estilos)) return erro("estilo", ESTILO_INVALIDO);
  values.estilo = estilo;
  const alfa = validateAlfa(fields.alfa_preenchimento);
  if (!alfa.ok) return erro("alfa_preenchimento", alfa.message);
  values.alfa_preenchimento = alfa.value;
  return { ok: true, values };
}

// Texto que a API aceita: sempre com ponto e sem expoente (String(1e-7) daria "1e-7").
function alfaTexto(alfa) {
  const texto = String(alfa);
  return /e/i.test(texto) ? alfa.toFixed(20).replace(/0+$/, "") : texto;
}

/** Checagem rápida no navegador; o servidor confere o conteúdo real da imagem. */
export function validateLogo(file) {
  if (!file) return { ok: true };
  if (!LOGO_TIPOS.includes(file.type)) return { ok: false, message: "Logo deve ser uma imagem PNG ou JPEG." };
  if (!(file.size > 0)) return { ok: false, message: "A logo está vazia." };
  if (file.size > LOGO_MAX_BYTES) return { ok: false, message: "Logo maior que 2 MB." };
  return { ok: true };
}

/** Campos do multipart: só o que difere do padrão, para o envio sem personalização ficar igual ao de antes. */
export function pranchaFields(fields, estilos = []) {
  const r = validatePrancha(fields, estilos);
  if (!r.ok) return [];
  return Object.entries(r.values)
    .filter(([campo, valor]) => valor !== PRANCHA_PADRAO[campo])
    .map(([campo, valor]) => [campo, campo === "alfa_preenchimento" ? alfaTexto(valor) : valor]);
}

/** Estilo Leaflet do polígono: o mesmo do mapa.pdf daquele job (cores, espessura e alfa8).
 *  Job antigo ou valor adulterado cai no padrão; o contorno é sempre opaco. */
export function polygonStyle(prancha, estilos = []) {
  const { contorno, preenchimento } = polygonColors(prancha);
  const estilo = estiloDoCatalogo(prancha?.estilo, estilos) ?? estiloDoCatalogo(PRANCHA_PADRAO.estilo, estilos);
  const alfa = validateAlfa(prancha?.alfa_preenchimento);
  return {
    color: contorno,
    fillColor: preenchimento,
    fillOpacity: alfa8(alfa.ok ? alfa.value : PRANCHA_PADRAO.alfa_preenchimento) / 255,
    weight: estilo?.espessura_px ?? ESPESSURA_PADRAO_PX,
    opacity: 1,
  };
}

/** Cores do polígono no navegador: as mesmas do mapa.pdf daquele job (ou as padrão). */
export function polygonColors(prancha) {
  const cor = (valor, padrao) => (typeof valor === "string" && COR.test(valor) ? valor.toUpperCase() : padrao);
  return {
    contorno: cor(prancha?.cor_contorno, PRANCHA_PADRAO.cor_contorno),
    preenchimento: cor(prancha?.cor_preenchimento, PRANCHA_PADRAO.cor_preenchimento),
  };
}

"""Catálogo de camadas do mapa, estilos do polígono e regra do alfa do preenchimento.

Única fonte de verdade: a API serve `catalogo_publico()` e o navegador não tem lista
própria. Só entra como "disponivel" o que tem fonte real; o resto aparece desabilitado,
sem URL. Nenhuma camada com rede é exportada: o mapa.pdf só desenha o polígono.
"""

import math
import re
from dataclasses import asdict, dataclass

from geolume_worker.errors import InvalidInputError


@dataclass(frozen=True)
class Atribuicao:
    texto: str
    url: str


@dataclass(frozen=True)
class Fonte:
    nome: str
    licenca: str
    termos_url: str
    atribuicao: Atribuicao
    atualizacao: str


@dataclass(frozen=True)
class Camada:
    id: str
    nome: str
    tipo: str  # "vetor" | "raster" | "nenhum"
    grupo: str  # "base" (escolha única) | "sobreposicao"
    situacao: str  # "disponivel" | "depende_fonte_oficial" | "planejada"
    ordem: int  # maior fica por cima
    fonte: Fonte | None = None
    url: str | None = None
    max_zoom: int | None = None
    exporta_pdf: bool = False
    visivel: bool = False
    aviso: str | None = None


_OSM = Fonte(
    nome="OpenStreetMap",
    licenca="ODbL",
    termos_url="https://www.openstreetmap.org/copyright",
    atribuicao=Atribuicao("© Contribuidores do OpenStreetMap", "https://www.openstreetmap.org/copyright"),
    atualizacao="contínua",
)
_SEM_FONTE = "Fonte oficial não definida"

CAMADAS = (
    Camada("ruas_osm", "Mapa de ruas (OpenStreetMap)", "raster", "base", "disponivel", 0, fonte=_OSM,
           url="https://tile.openstreetmap.org/{z}/{x}/{y}.png", max_zoom=19, visivel=True,
           aviso="Somente na pré-visualização — não entra no PDF"),
    Camada("nenhum", "Sem mapa-base", "nenhum", "base", "disponivel", 0),
    Camada("poligono", "Polígono do imóvel", "vetor", "sobreposicao", "disponivel", 100,
           exporta_pdf=True, visivel=True),
    Camada("limites_municipais", "Limites municipais", "vetor", "sobreposicao", "depende_fonte_oficial", 50,
           aviso=_SEM_FONTE),
    Camada("hidrografia", "Hidrografia", "vetor", "sobreposicao", "depende_fonte_oficial", 40, aviso=_SEM_FONTE),
    Camada("rodovias", "Rodovias", "vetor", "sobreposicao", "depende_fonte_oficial", 45, aviso=_SEM_FONTE),
    Camada("satelite", "Satélite", "raster", "base", "depende_fonte_oficial", 0, aviso="Sem provedor licenciado"),
    Camada("topografia", "Curvas de nível / relevo", "vetor", "sobreposicao", "planejada", 30, aviso="Planejada"),
    Camada("edificacoes", "Edificações", "vetor", "sobreposicao", "planejada", 60, aviso="Planejada"),
)


@dataclass(frozen=True)
class Estilo:
    id: str
    nome: str
    contorno: str
    preenchimento: str
    espessura_mm: float  # mapa.pdf
    espessura_px: int  # pré-visualização


ESTILOS = (
    Estilo("padrao", "Padrão", "#C80000", "#FFC800", 0.6, 3),
    Estilo("tecnico", "Técnico", "#1F2937", "#9CA3AF", 0.35, 2),
    Estilo("pb", "Preto e branco", "#000000", "#FFFFFF", 0.5, 2),
)
ESTILO_PADRAO = "padrao"
ALFA_PADRAO = 0.35
# Só dígitos ASCII com ponto decimal: "0,5", "1e-1", "NaN", "٠.٥" e afins são recusados. Regras iguais às de
# prancha.js, conferidas pela tabela tests/fixtures/alfa_casos.json.
_ALFA_TEXTO = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
# Só espaço ASCII é aparado: str.strip() e String.trim() discordam no resto (U+001C, U+FEFF, U+00A0).
_ESPACOS = " \t\n\r\f\v"


def estilo_por_id(estilo_id: str) -> Estilo:
    for estilo in ESTILOS:
        if estilo.id == estilo_id:
            return estilo
    raise KeyError(estilo_id)


def _alfa_invalido() -> InvalidInputError:
    # Mensagem fixa: o valor recebido nunca volta ao cliente.
    return InvalidInputError("alfa_invalido", "Opacidade do preenchimento deve estar entre 0 e 1.")


def validar_alfa(valor) -> float:
    """Alfa do preenchimento em [0, 1]; ausente ou vazio é o padrão. Inválido é recusado, nunca corrigido."""
    if valor is None:
        return ALFA_PADRAO
    if isinstance(valor, str):
        valor = valor.strip(_ESPACOS)
        if not valor:
            return ALFA_PADRAO
        if not _ALFA_TEXTO.fullmatch(valor):
            raise _alfa_invalido()
        valor = float(valor)
    elif isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise _alfa_invalido()
    valor = float(valor)
    if not math.isfinite(valor) or not 0 <= valor <= 1:
        raise _alfa_invalido()
    return valor


def alfa8(alfa: float) -> int:
    """Alfa em 0–255: o QGIS usa este valor e o navegador usa alfa8 / 255, então tela e PDF coincidem."""
    return math.floor(alfa * 255 + 0.5)  # metade para cima, como Math.round (round() iria ao par)


def catalogo_publico() -> dict:
    """Catálogo como a API devolve: só tipos JSON."""
    return {
        "camadas": [asdict(camada) for camada in CAMADAS],
        "estilos": [asdict(estilo) for estilo in ESTILOS],
        "alfa_padrao": ALFA_PADRAO,
    }

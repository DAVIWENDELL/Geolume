"""Opções da prancha (mapa.pdf): validadas na API e de novo no worker.

Tudo é opcional; sem opções a prancha sai exatamente como antes. A logo é validada
pelo conteúdo (só PNG e JPEG reais), com as dimensões conferidas no cabeçalho antes
de decodificar, e gravada já normalizada como PNG de até 1000 px.
"""

import io
import os
import re
import unicodedata
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageOps

from geolume_worker.camadas import ALFA_PADRAO, ESTILO_PADRAO, ESTILOS, validar_alfa
from geolume_worker.errors import InvalidInputError

TEXTO_MAX = 100
COR_CONTORNO_PADRAO = "#C80000"
COR_PREENCHIMENTO_PADRAO = "#FFC800"
LEGENDAS = ("nenhuma", "lateral", "inferior")

LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_MAX_LADO = 1000
LOGO_FORMATOS = ("PNG", "JPEG")
# Limites da imagem de origem, conferidos antes de decodificar (bomba de descompressão).
LOGO_ORIGEM_MAX_LADO = 10_000
LOGO_ORIGEM_MAX_PIXELS = 25_000_000

_COR = re.compile(r"#[0-9A-Fa-f]{6}")
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Controles bidi (embutir, isolar, marcas LRM/RLM/ALM) e separadores de linha/parágrafo.
_PROIBIDOS = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f\u061c\u2028\u2029")


@dataclass(frozen=True)
class Prancha:
    projeto: str | None = None
    responsavel: str | None = None
    cor_contorno: str = COR_CONTORNO_PADRAO
    cor_preenchimento: str = COR_PREENCHIMENTO_PADRAO
    legenda: str = "nenhuma"
    logo: bool = False
    estilo: str = ESTILO_PADRAO
    alfa_preenchimento: float = ALFA_PADRAO

    @property
    def padrao(self) -> bool:
        return self == Prancha()

    def como_dict(self) -> dict:
        return asdict(self)


def _texto(valor, campo: str, rotulo: str) -> str | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise InvalidInputError(f"{campo}_invalido", f"{rotulo} deve ser texto.")
    valor = valor.strip()
    if any(unicodedata.category(c) == "Cc" or c in _PROIBIDOS for c in valor):
        raise InvalidInputError(f"{campo}_invalido", f"{rotulo} não pode ter quebras de linha nem caracteres de controle.")
    if len(valor) > TEXTO_MAX:
        raise InvalidInputError(f"{campo}_invalido", f"{rotulo} deve ter no máximo {TEXTO_MAX} caracteres.")
    return valor or None


def _cor(valor, padrao: str) -> str:
    if valor is None or valor == "":
        return padrao
    if not isinstance(valor, str) or not _COR.fullmatch(valor):
        raise InvalidInputError("cor_invalida", "Cor deve estar no formato #RRGGBB.")
    return valor.upper()


def validar_prancha(dados: dict | None) -> Prancha:
    """Valida e normaliza as opções; chaves desconhecidas são ignoradas."""
    dados = dados or {}
    legenda = dados.get("legenda")
    if legenda is None or legenda == "":
        legenda = "nenhuma"
    if not isinstance(legenda, str) or legenda not in LEGENDAS:
        # Mensagem fixa: o valor recebido nunca volta ao cliente.
        raise InvalidInputError("legenda_invalida", "Legenda deve ser 'nenhuma', 'lateral' ou 'inferior'.")
    logo = dados.get("logo", False)
    if not isinstance(logo, bool):
        raise InvalidInputError("logo_invalida", "Indicador de logo inválido.")
    estilo = dados.get("estilo")
    if estilo is None or estilo == "":
        estilo = ESTILO_PADRAO
    if not isinstance(estilo, str) or estilo not in {e.id for e in ESTILOS}:
        raise InvalidInputError("estilo_invalido", "Estilo do polígono inválido.")
    return Prancha(
        projeto=_texto(dados.get("projeto"), "projeto", "Nome do projeto"),
        responsavel=_texto(dados.get("responsavel"), "responsavel", "Responsável técnico"),
        cor_contorno=_cor(dados.get("cor_contorno"), COR_CONTORNO_PADRAO),
        cor_preenchimento=_cor(dados.get("cor_preenchimento"), COR_PREENCHIMENTO_PADRAO),
        legenda=legenda,
        logo=logo,
        estilo=estilo,
        alfa_preenchimento=validar_alfa(dados.get("alfa_preenchimento")),
    )


def caminho_logo(output_dir: Path, job_id: str) -> Path:
    """Único lugar da logo de um job: derivado só do job_id, nunca do nome enviado."""
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("job_id inválido para a logo")
    return Path(output_dir) / "logos" / f"{job_id}.png"


def conferir_logo_normalizada(caminho: Path) -> None:
    """No worker: a logo do job ainda é o PNG normalizado que a API gravou."""
    if not caminho.exists() and not caminho.is_symlink():
        raise InvalidInputError("logo_ausente", "Logo do job não encontrada.")
    try:
        if caminho.is_symlink() or not caminho.is_file():
            raise ValueError("não é arquivo regular")
        with Image.open(caminho, formats=("PNG",)) as imagem:
            if max(imagem.size) > LOGO_MAX_LADO:
                raise ValueError("maior que o normalizado")
            imagem.verify()
    except Exception:  # noqa: BLE001
        raise _logo_invalida() from None


def _logo_invalida() -> InvalidInputError:
    return InvalidInputError("logo_invalida", "Logo deve ser uma imagem PNG ou JPEG válida.")


def normalizar_logo(conteudo: bytes, destino: Path) -> None:
    """Valida a logo pelo conteúdo e grava só a versão normalizada (PNG, lado ≤ 1000 px)."""
    if len(conteudo) > LOGO_MAX_BYTES:
        raise InvalidInputError("logo_grande", "Logo maior que 2 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # formats= impede o Pillow de tentar qualquer outro decodificador.
            with Image.open(io.BytesIO(conteudo), formats=LOGO_FORMATOS) as imagem:
                largura, altura = imagem.size  # só o cabeçalho foi lido até aqui
                if (max(largura, altura) > LOGO_ORIGEM_MAX_LADO
                        or largura * altura > LOGO_ORIGEM_MAX_PIXELS):
                    raise InvalidInputError("logo_dimensoes", "Logo com dimensões grandes demais.")
                imagem.load()
                normalizada = ImageOps.exif_transpose(imagem).convert("RGBA")
    except InvalidInputError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidInputError("logo_dimensoes", "Logo com dimensões grandes demais.") from None
    except Exception:  # noqa: BLE001 — qualquer falha do decodificador é arquivo inválido
        raise _logo_invalida() from None
    normalizada.thumbnail((LOGO_MAX_LADO, LOGO_MAX_LADO), Image.Resampling.LANCZOS)
    temporario = destino.with_name(f".{destino.name}.tmp")
    try:
        normalizada.save(temporario, "PNG")
        os.replace(temporario, destino)
    finally:
        temporario.unlink(missing_ok=True)

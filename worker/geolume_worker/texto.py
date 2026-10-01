"""Texto literal para rótulos do QGIS.

`QgsLayoutItemLabel` avalia todo trecho `[% ... %]` como expressão (inclusive `env()`),
e não há opção para desligar isso. Cada `[` vira a expressão constante `[% '[' %]`:
o QGIS avalia o texto uma única vez, então nenhum `[%` do valor original sobra para
ser interpretado, e o PDF mostra exatamente o texto recebido.
"""

import re
import unicodedata

_COLCHETE = "[% '[' %]"
_ESPACOS = re.compile(r"\s+")


def texto_literal(texto: str) -> str:
    return str(texto).replace("[", _COLCHETE)


_JUNTORES = "\u200c\u200d"  # ZWNJ e ZWJ: fazem parte da grafia (emoji compostos, escritas)


def uma_linha(valor: object) -> str:
    """Valor do GeoJSON como uma linha: controle (quebra, tab, CR) vira espaço e caractere
    de formatação (bidi, largura zero) sai, para não empurrar nem inverter outros rótulos."""
    texto = "".join(
        " " if unicodedata.category(c) == "Cc" else c
        for c in str(valor)
        if unicodedata.category(c) != "Cf" or c in _JUNTORES
    )
    return _ESPACOS.sub(" ", texto).strip()


def informado(valor: object) -> str | None:
    """Valor em uma linha, ou None se ausente: nulo (None ou NULL do QGIS) ou vazio depois de limpo."""
    if valor is None or getattr(valor, "isNull", lambda: False)():
        return None
    return uma_linha(valor) or None

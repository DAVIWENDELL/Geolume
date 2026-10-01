"""Medidas de texto em mm pelo próprio rótulo do QGIS (DejaVu Sans), sem estimativa.

Mapa e memorial decidem quebra, corte e paginação com estas funções; o rótulo medido
nunca entra no layout.
"""

from qgis.core import QgsLayoutItemLabel, QgsPrintLayout, QgsTextFormat
from qgis.PyQt.QtGui import QFont

from geolume_worker.texto import texto_literal

RETICENCIAS = "…"


def medir(layout: QgsPrintLayout, texto: str, tamanho: float) -> tuple[float, float]:
    """Largura e altura (mm) do rótulo ajustado ao texto, como o layout o desenharia."""
    label = QgsLayoutItemLabel(layout)
    label.setText(texto_literal(texto))
    formato = QgsTextFormat()
    formato.setFont(QFont("DejaVu Sans"))
    formato.setSize(tamanho)
    label.setTextFormat(formato)
    label.adjustSizeToText()
    tamanho_mm = label.sizeWithUnits()
    return tamanho_mm.width(), tamanho_mm.height()


def _cabe(layout: QgsPrintLayout, texto: str, tamanho: float, largura: float) -> bool:
    return medir(layout, texto, tamanho)[0] <= largura


def _maior_prefixo(layout: QgsPrintLayout, texto: str, tamanho: float, largura: float, sufixo: str = "") -> int:
    """Maior n (≥ 1) com texto[:n] + sufixo dentro da largura; busca binária."""
    baixo, alto = 1, len(texto)
    while baixo < alto:
        meio = (baixo + alto + 1) // 2
        if _cabe(layout, texto[:meio] + sufixo, tamanho, largura):
            baixo = meio
        else:
            alto = meio - 1
    return baixo


def _quebrar_linha(layout: QgsPrintLayout, linha: str, tamanho: float, largura: float) -> list[str]:
    linhas: list[str] = []
    atual = ""
    for palavra in linha.split():
        candidata = f"{atual} {palavra}" if atual else palavra
        if _cabe(layout, candidata, tamanho, largura):
            atual = candidata
            continue
        if atual:
            linhas.append(atual)
        # Palavra maior que a largura: quebra por caractere.
        while not _cabe(layout, palavra, tamanho, largura):
            n = _maior_prefixo(layout, palavra, tamanho, largura)
            linhas.append(palavra[:n])
            palavra = palavra[n:]
        atual = palavra
    linhas.append(atual)
    return linhas


def quebrar_por_largura(layout: QgsPrintLayout, texto: str, tamanho: float, largura: float) -> list[str]:
    """Quebra gulosa por palavras medidas; parágrafos (linha em branco) viram linha vazia."""
    linhas: list[str] = []
    for indice, paragrafo in enumerate(texto.split("\n\n")):
        if indice:
            linhas.append("")
        for linha in paragrafo.split("\n"):
            linhas.extend(_quebrar_linha(layout, linha, tamanho, largura))
    return linhas


def cortar_para_caber(layout: QgsPrintLayout, texto: str, tamanho: float, largura: float) -> str:
    """O texto inteiro se couber; senão o maior prefixo seguido de reticências."""
    if _cabe(layout, texto, tamanho, largura):
        return texto
    return texto[:_maior_prefixo(layout, texto, tamanho, largura, RETICENCIAS)] + RETICENCIAS


def linhas_que_cabem(
    layout: QgsPrintLayout, cabecalho: list[str], linhas: list[str], tamanho: float, altura: float
) -> int:
    """Maior k com cabeçalho + linhas[:k] num só rótulo de altura ≤ altura (mm)."""
    baixo, alto = 0, len(linhas)
    while baixo < alto:
        meio = (baixo + alto + 1) // 2
        if medir(layout, "\n".join(cabecalho + linhas[:meio]), tamanho)[1] <= altura:
            baixo = meio
        else:
            alto = meio - 1
    return baixo

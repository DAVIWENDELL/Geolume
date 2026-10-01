"""Verificação de PDFs pelo poppler: páginas, palavras com caixa, margens e sobreposição (pt, origem no topo)."""

import html
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MM = 72 / 25.4  # pt por mm

_PAGINA = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
_PALAVRA = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')


@dataclass(frozen=True)
class Palavra:
    pagina: int
    x0: float
    y0: float
    x1: float
    y1: float
    texto: str
    largura_pagina: float
    altura_pagina: float


def paginas(pdf: Path) -> int:
    saida = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"^Pages:\s+(\d+)", saida, re.M).group(1))


def texto(pdf: Path, *opcoes: str) -> str:
    return subprocess.run(["pdftotext", *opcoes, str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def palavras(pdf: Path) -> list[Palavra]:
    bbox = texto(pdf, "-bbox")
    resultado = []
    for numero, (largura, altura, corpo) in enumerate(_PAGINA.findall(bbox), start=1):
        for x0, y0, x1, y1, conteudo in _PALAVRA.findall(corpo):
            resultado.append(Palavra(numero, float(x0), float(y0), float(x1), float(y1), html.unescape(conteudo),
                                     float(largura), float(altura)))
    return resultado


def fora_da_margem(pdf: Path, margem_pt: float, *, topo_pt: float | None = None, base_pt: float | None = None,
                   esquerda_pt: float | None = None, direita_pt: float | None = None) -> list[Palavra]:
    """Palavras que passam da margem (por lado, se informado) ou da própria página."""
    topo = margem_pt if topo_pt is None else topo_pt
    base = margem_pt if base_pt is None else base_pt
    esquerda = margem_pt if esquerda_pt is None else esquerda_pt
    direita = margem_pt if direita_pt is None else direita_pt
    return [p for p in palavras(pdf)
            if p.x0 < esquerda or p.y0 < topo or p.x1 > p.largura_pagina - direita or p.y1 > p.altura_pagina - base]


def _intersecao(a: Palavra, b: Palavra) -> float:
    largura = min(a.x1, b.x1) - max(a.x0, b.x0)
    altura = min(a.y1, b.y1) - max(a.y0, b.y0)
    return largura * altura if largura > 0 and altura > 0 else 0.0


def sobrepostas(pdf: Path, tolerancia_pt2: float = 0.5) -> list[tuple[Palavra, Palavra]]:
    """Pares de palavras na mesma página com interseção de área maior que a tolerância."""
    pares = []
    por_pagina: dict[int, list[Palavra]] = {}
    for p in palavras(pdf):
        por_pagina.setdefault(p.pagina, []).append(p)
    for lista in por_pagina.values():
        lista.sort(key=lambda p: p.y0)
        for i, a in enumerate(lista):
            for b in lista[i + 1:]:
                if b.y0 >= a.y1:
                    break
                if _intersecao(a, b) > tolerancia_pt2:
                    pares.append((a, b))
    return pares

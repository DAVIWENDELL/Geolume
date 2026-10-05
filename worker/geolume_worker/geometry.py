"""Funções geométricas puras (sem QGIS)."""

import math
from dataclasses import dataclass

from geolume_worker.errors import InvalidInputError

# SIRGAS 2000 / UTM: sul 31978..31985 (fusos 18..25), norte 31972..31977 (fusos 17..22).
_FUSOS_SUL = range(18, 26)
_FUSOS_NORTE = range(17, 23)


def utm_epsg_for(lon: float, lat: float) -> int:
    fuso = int(math.floor((lon + 180.0) / 6.0)) + 1
    if lat < 0 and fuso in _FUSOS_SUL:
        return 31960 + fuso
    if lat >= 0 and fuso in _FUSOS_NORTE:
        return 31955 + fuso
    raise InvalidInputError(
        "fora_da_cobertura",
        f"Coordenada ({lon}, {lat}) fora dos fusos SIRGAS 2000 / UTM suportados.",
    )


def grid_azimuth_deg(e1: float, n1: float, e2: float, n2: float) -> float:
    return math.degrees(math.atan2(e2 - e1, n2 - n1)) % 360.0


def format_dms(deg: float) -> str:
    total_s = round(deg * 3600) % (360 * 3600)
    graus, resto = divmod(total_s, 3600)
    minutos, segundos = divmod(resto, 60)
    return f"{graus}°{minutos:02d}'{segundos:02d}\""


_HEMISFERIOS = {"lat": ("S", "N"), "lon": ("O", "L")}


def format_gms(valor: float, eixo: str) -> str:
    """Latitude ("lat") ou longitude ("lon") em GMS com hemisfério S/N ou O/L; segundos com 2 casas."""
    if eixo not in _HEMISFERIOS:
        raise ValueError(f"eixo inválido: {eixo}")
    # Arredonda em centésimos de segundo inteiros (metade para cima): 59,995" vira o minuto seguinte, nunca 60".
    # O round(…, 6) tira o ruído do float antes do floor.
    centesimos = math.floor(round(abs(valor) * 360_000, 6) + 0.5)
    graus, resto = divmod(centesimos, 360_000)
    minutos, resto = divmod(resto, 6_000)
    segundos, fracao = divmod(resto, 100)
    hemisferio = _HEMISFERIOS[eixo][0 if valor < 0 and centesimos else 1]  # zero arredondado é N/L
    return f"{graus}°{minutos:02d}'{segundos:02d}.{fracao:02d}\" {hemisferio}"


def fuso_utm(epsg: int) -> tuple[int, str]:
    """Fuso e hemisfério de um EPSG SIRGAS 2000 / UTM suportado (inverso de utm_epsg_for)."""
    if epsg - 31960 in _FUSOS_SUL:
        return epsg - 31960, "Sul"
    if epsg - 31955 in _FUSOS_NORTE:
        return epsg - 31955, "Norte"
    raise ValueError(f"EPSG fora dos fusos SIRGAS 2000 / UTM suportados: {epsg}")


def denominador_legivel(calculado: float) -> int:
    """Denominador da escala arredondado para cima com 2 dígitos significativos (173 412 → 180 000).

    Para cima: o mapa só se afasta, e o polígono continua cabendo.
    """
    if not math.isfinite(calculado) or calculado <= 0:
        raise ValueError(f"escala inválida: {calculado}")
    calculado = max(round(calculado, 6), 1)  # tira o ruído do float antes do ceil
    passo = 10 ** max(int(math.floor(math.log10(calculado))) - 1, 0)
    return math.ceil(calculado / passo) * passo


def formatar_escala(denominador: int) -> str:
    """1:180.000 — ponto como separador de milhar."""
    return f"1:{denominador:,}".replace(",", ".")


@dataclass(frozen=True)
class Vertex:
    id: str
    e: float
    n: float
    azimute: str
    distancia_m: float


def _mesmo_ponto(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return round(a[0], 3) == round(b[0], 3) and round(a[1], 3) == round(b[1], 3)


def vertex_table(ring: list[tuple[float, float]]) -> list[Vertex]:
    """Tabela V1..Vn; aceita anel com ponto de fechamento e remove duplicados consecutivos."""
    pontos: list[tuple[float, float]] = []
    for p in ring:
        if not pontos or not _mesmo_ponto(p, pontos[-1]):
            pontos.append((p[0], p[1]))
    if len(pontos) > 1 and _mesmo_ponto(pontos[0], pontos[-1]):
        pontos.pop()

    vertices = []
    for i, (e, n) in enumerate(pontos):
        e2, n2 = pontos[(i + 1) % len(pontos)]
        vertices.append(
            Vertex(
                id=f"V{i + 1}",
                e=round(e, 2),
                n=round(n, 2),
                azimute=format_dms(grid_azimuth_deg(e, n, e2, n2)),
                distancia_m=round(math.hypot(e2 - e, n2 - n), 2),
            )
        )
    return vertices

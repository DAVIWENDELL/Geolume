"""GeoJSON de teste: polígono regular com n vértices (raio ~100 m, Brasília, EPSG:4326)."""

import json
import math
from pathlib import Path

CENTRO = (-47.9, -15.8)
RAIO_GRAUS = 0.0009


def gerar(destino: Path, n: int, propriedades: dict | None = None) -> Path:
    anel = [
        [round(CENTRO[0] + RAIO_GRAUS * math.cos(2 * math.pi * k / n), 8),
         round(CENTRO[1] + RAIO_GRAUS * math.sin(2 * math.pi * k / n), 8)]
        for k in range(n)
    ]
    anel.append(anel[0])
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": propriedades if propriedades is not None else {"nome_imovel": "Teste"},
            "geometry": {"type": "Polygon", "coordinates": [anel]},
        }],
    }
    destino = Path(destino)
    destino.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    return destino

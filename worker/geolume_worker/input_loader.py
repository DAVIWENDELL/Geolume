"""Leitura e validação da entrada GeoJSON (1 polígono simples, EPSG:4326/4674)."""

import json
from dataclasses import dataclass
from pathlib import Path

from qgis.core import Qgis, QgsGeometry, QgsVectorLayer

from geolume_worker.errors import InvalidInputError

MAX_BYTES = 10 * 1024 * 1024
MAX_VERTICES = 5000
CRS_ACEITOS = {"EPSG:4326", "EPSG:4674"}


@dataclass
class LoadedInput:
    layer: QgsVectorLayer
    geometry: QgsGeometry
    source_crs: str
    properties: dict[str, object]


def load_input(path: Path) -> LoadedInput:
    path = Path(path)
    if not path.is_file():
        raise InvalidInputError("arquivo_nao_encontrado", f"Arquivo {path.name} não encontrado.")
    if path.stat().st_size > MAX_BYTES:
        raise InvalidInputError("arquivo_muito_grande", f"Arquivo maior que {MAX_BYTES // (1024 * 1024)} MB.")
    try:
        dados = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidInputError("json_invalido", f"Arquivo não é JSON válido: {exc}") from exc
    if isinstance(dados, dict) and dados.get("type") == "FeatureCollection" and not dados.get("features"):
        raise InvalidInputError("sem_feicoes", "O GeoJSON não contém feições.")

    layer = QgsVectorLayer(str(path), "entrada", "ogr")
    if not layer.isValid():
        raise InvalidInputError("json_invalido", "O arquivo não é um GeoJSON reconhecido.")
    features = list(layer.getFeatures())
    if not features:
        raise InvalidInputError("sem_feicoes", "O GeoJSON não contém feições.")
    if len(features) > 1:
        raise InvalidInputError("multiplas_feicoes", f"Esperada 1 feição, recebidas {len(features)}.")

    geometry = QgsGeometry(features[0].geometry())
    if geometry.isEmpty() or geometry.type() != Qgis.GeometryType.Polygon:
        raise InvalidInputError("geometria_nao_poligonal", "A feição precisa ser um polígono.")
    if geometry.isMultipart():
        if geometry.constGet().numGeometries() > 1:
            raise InvalidInputError("multiplas_partes", "O polígono tem mais de uma parte.")
        geometry.convertToSingleType()
    aneis = geometry.asPolygon()
    if len(aneis) > 1:
        raise InvalidInputError("poligono_com_furos", "Polígonos com furos não são suportados.")
    if not geometry.isGeosValid():
        raise InvalidInputError("geometria_invalida", "O polígono é inválido (ex.: autointerseção).")
    if len(aneis[0]) - 1 > MAX_VERTICES:
        raise InvalidInputError("excesso_de_vertices", f"O polígono excede {MAX_VERTICES} vértices.")

    source_crs = layer.crs().authid()
    if source_crs not in CRS_ACEITOS:
        raise InvalidInputError("crs_nao_suportado", f"CRS {source_crs or 'desconhecido'} não suportado.")
    properties = dict(zip(layer.fields().names(), features[0].attributes()))
    return LoadedInput(layer=layer, geometry=geometry, source_crs=source_crs, properties=properties)

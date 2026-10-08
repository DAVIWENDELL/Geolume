"""Leitura e validação da entrada GeoJSON (1 polígono simples, EPSG:4326/4674)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from qgis.core import Qgis, QgsGeometry, QgsVectorLayer

from geolume_worker.errors import InvalidInputError
from geolume_worker.geometry import validar_coordenadas

MAX_BYTES = 10 * 1024 * 1024
MAX_VERTICES = 5000
CRS_ACEITOS = {"EPSG:4326", "EPSG:4674"}
# Nomes do membro "crs" (GeoJSON de 2008) que o OGR lê: EPSG:n, a URN EPSG e a URN CRS84 (= EPSG:4326).
_EPSG_DECLARADO = re.compile(r"(?:EPSG:|URN:OGC:DEF:CRS:EPSG:[0-9.]*:)([0-9]{1,6})")
_CRS84_DECLARADO = "URN:OGC:DEF:CRS:OGC:1.3:CRS84"


@dataclass
class LoadedInput:
    layer: QgsVectorLayer
    geometry: QgsGeometry
    source_crs: str
    properties: dict[str, object]


def _crs_nao_suportado(nome: str | None) -> InvalidInputError:
    aceitos = "Use EPSG:4326 (WGS 84) ou EPSG:4674 (SIRGAS 2000)."
    if nome:
        return InvalidInputError("crs_nao_suportado", f"Sistema de coordenadas {nome} não suportado. {aceitos}")
    return InvalidInputError(
        "crs_nao_suportado", f"Sistema de coordenadas declarado no GeoJSON não reconhecido. {aceitos}"
    )


def _conferir_crs_declarado(dados: object) -> None:
    """Confere o membro "crs" antes do OGR: ele cai em EPSG:4326 sem avisar e baixa o href de um CRS "link"."""
    if not isinstance(dados, dict) or dados.get("crs") is None:
        return
    crs = dados["crs"]
    propriedades = crs.get("properties") if isinstance(crs, dict) and crs.get("type") == "name" else None
    nome = propriedades.get("name") if isinstance(propriedades, dict) else None
    if isinstance(nome, str):
        nome = nome.strip().upper()
        if nome == _CRS84_DECLARADO:
            return
        if epsg := _EPSG_DECLARADO.fullmatch(nome):
            authid = f"EPSG:{int(epsg[1])}"  # só dígitos do cliente voltam na mensagem
            if authid in CRS_ACEITOS:
                return
            raise _crs_nao_suportado(authid)
    raise _crs_nao_suportado(None)


def load_input(path: Path) -> LoadedInput:
    path = Path(path)
    if not path.is_file():
        raise InvalidInputError("arquivo_nao_encontrado", f"Arquivo {path.name} não encontrado.")
    if path.stat().st_size > MAX_BYTES:
        raise InvalidInputError("arquivo_muito_grande", f"Arquivo maior que {MAX_BYTES // (1024 * 1024)} MB.")
    # A mensagem vai ao cliente: diz a causa sem repassar o texto bruto da exceção do Python.
    try:
        texto = path.read_text(encoding="utf-8-sig")  # aceita o BOM que editores do Windows gravam
    except UnicodeDecodeError as exc:
        raise InvalidInputError("json_invalido", "O arquivo precisa estar em UTF-8.") from exc
    if not texto.strip():
        raise InvalidInputError("json_invalido", "O arquivo está vazio.")
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise InvalidInputError(
            "json_invalido", f"O arquivo não é um JSON válido (erro na linha {exc.lineno}, coluna {exc.colno})."
        ) from exc
    if isinstance(dados, dict) and dados.get("type") == "FeatureCollection" and not dados.get("features"):
        raise InvalidInputError("sem_feicoes", "O GeoJSON não contém feições.")
    _conferir_crs_declarado(dados)

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
        raise _crs_nao_suportado(source_crs)
    validar_coordenadas([(p.x(), p.y()) for p in aneis[0][:-1]])  # anel sem o ponto de fechamento
    properties = dict(zip(layer.fields().names(), features[0].attributes()))
    return LoadedInput(layer=layer, geometry=geometry, source_crs=source_crs, properties=properties)

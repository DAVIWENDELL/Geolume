"""Reprojeção para SIRGAS 2000 / UTM e cálculo de área, perímetro e vértices."""

from dataclasses import dataclass

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject

from geolume_worker.errors import InvalidInputError
from geolume_worker.geometry import Vertex, utm_epsg_for, vertex_table
from geolume_worker.input_loader import LoadedInput


@dataclass
class ParcelSummary:
    epsg: int
    area_ha: float
    perimetro_m: float
    vertices: list[Vertex]
    geometry_utm: QgsGeometry
    properties: dict[str, object]
    centroide_geo: tuple[float, float]  # (lon, lat) em SIRGAS 2000 geográficas, EPSG:4674


def process(loaded: LoadedInput) -> ParcelSummary:
    centro = loaded.geometry.centroid().asPoint()
    epsg = utm_epsg_for(centro.x(), centro.y())

    geometry_utm = QgsGeometry(loaded.geometry)
    transform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem(loaded.source_crs),
        QgsCoordinateReferenceSystem(f"EPSG:{epsg}"),
        QgsProject.instance().transformContext(),
    )
    geometry_utm.transform(transform)
    # Centroide calculado no plano UTM e levado ao geográfico: o mesmo ponto do E/N mostrado no mapa.
    para_geo = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem(f"EPSG:{epsg}"),
        QgsCoordinateReferenceSystem("EPSG:4674"),
        QgsProject.instance().transformContext(),
    )
    centroide = para_geo.transform(geometry_utm.centroid().asPoint())

    anel = [(p.x(), p.y()) for p in geometry_utm.asPolygon()[0]]
    vertices = vertex_table(anel)
    # Válido em graus, mas a tabela arredonda E/N a 3 casas e une consecutivos iguais: com menos de 3 não há polígono.
    if len(vertices) < 3:
        restam = ("resta 1 vértice; é necessário" if len(vertices) == 1
                  else f"restam {len(vertices)} vértices; são necessários")
        raise InvalidInputError(
            "poligono_muito_pequeno",
            f"O polígono é pequeno demais: após arredondar as coordenadas para milímetros, {restam} pelo menos 3.",
        )
    return ParcelSummary(
        epsg=epsg,
        area_ha=round(geometry_utm.area() / 10_000, 4),
        perimetro_m=round(geometry_utm.length(), 2),
        vertices=vertices,
        geometry_utm=geometry_utm,
        properties=loaded.properties,
        centroide_geo=(centroide.x(), centroide.y()),
    )

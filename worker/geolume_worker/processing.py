"""Reprojeção para SIRGAS 2000 / UTM e cálculo de área, perímetro e vértices."""

from dataclasses import dataclass

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject

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

    anel = [(p.x(), p.y()) for p in geometry_utm.asPolygon()[0]]
    return ParcelSummary(
        epsg=epsg,
        area_ha=round(geometry_utm.area() / 10_000, 4),
        perimetro_m=round(geometry_utm.length(), 2),
        vertices=vertex_table(anel),
        geometry_utm=geometry_utm,
        properties=loaded.properties,
    )

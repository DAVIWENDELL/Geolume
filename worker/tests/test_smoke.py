import os

import pytest

qgis_core = pytest.importorskip("qgis.core")

from geolume_worker.qgis_session import qgis_session


def test_pyqgis_importa_e_versao_ltr():
    assert qgis_core.Qgis.version().startswith("3.44")


def test_plataforma_offscreen():
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"


def test_sessao_aninhada_reusa_instancia(qgis_app):
    # Usa a sessão global: recriar QgsApplication após exitQgis no mesmo processo não é suportado.
    with qgis_session() as a:
        assert a is qgis_app
        with qgis_session() as b:
            assert a is b


def test_sessao_permite_criar_camada(qgis_app):
    layer = qgis_core.QgsVectorLayer("Polygon?crs=EPSG:4674", "t", "memory")
    assert layer.isValid()

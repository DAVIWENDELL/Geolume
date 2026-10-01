import os
from pathlib import Path

import pytest

# No container os testes PyQGIS nunca podem ser pulados silenciosamente.
if os.environ.get("GEOLUME_REQUIRE_QGIS") == "1":
    import qgis.core  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def qgis_app():
    pytest.importorskip("qgis.core")
    from geolume_worker.qgis_session import qgis_session

    with qgis_session() as app:
        yield app

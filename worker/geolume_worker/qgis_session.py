"""Sessão QGIS headless reentrante: um initQgis por processo (base para o worker Celery)."""

from contextlib import contextmanager
from typing import Iterator

from qgis.core import QgsApplication

_app: QgsApplication | None = None
_refs = 0


def is_qgis_initialized() -> bool:
    return _app is not None


@contextmanager
def qgis_session() -> Iterator[QgsApplication]:
    global _app, _refs
    if _app is None:
        QgsApplication.setPrefixPath("/usr", True)
        _app = QgsApplication([], False)
        _app.initQgis()
    _refs += 1
    try:
        yield _app
    finally:
        _refs -= 1
        if _refs == 0:
            _app.exitQgis()
            _app = None

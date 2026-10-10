"""Artefatos mínimos que passam em integridade.conferir_artefatos, para testes sem QGIS."""

import json
from pathlib import Path

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def resultado(job_id: str) -> dict[str, object]:
    """Mesma forma do resultado.json de run_job (job.py)."""
    vertices = [{"id": f"V{i}", "e": 500000.0 + i, "n": 8000000.0 + i, "azimute": "90°00'00\"", "distancia_m": 10.0}
                for i in range(1, 5)]
    return {"versao_esquema": 1, "job_id": job_id, "entrada": "lote.geojson", "propriedades": {},
            "pdf_memorial": "memorial.pdf", "crs_saida": "EPSG:31983", "area_ha": 0.01, "perimetro_m": 40.0,
            "vertices": vertices,
            "metricas": {"fases_ms": {"carregar": 9.5, "processar": 0.1, "renderizar_pdf": 195.7, "total_job": 230.7},
                         "pico_rss_mb": 245.7, "rss_antes_mb": 223.8, "rss_depois_mb": 246.3}}


def gravar(pasta: Path, job_id: str, *nomes: str) -> Path:
    """Grava os artefatos pedidos (todos, sem nomes) válidos na pasta do job."""
    pasta.mkdir(parents=True, exist_ok=True)
    for nome in nomes or ("mapa.pdf", "memorial.pdf", "resultado.json"):
        if nome == "resultado.json":
            (pasta / nome).write_text(json.dumps(resultado(job_id), ensure_ascii=False), encoding="utf-8")
        else:
            (pasta / nome).write_bytes(PDF)
    return pasta

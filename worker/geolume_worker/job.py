"""Contrato de execução de um job: usado pela CLI hoje e por uma task Celery no futuro.

Não inicializa o QGIS: quem chama mantém uma `qgis_session()` aberta (1 por processo).
"""

import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from geolume_worker.input_loader import load_input
from geolume_worker.layout import export_map_pdf
from geolume_worker.memorial import export_memorial_pdf
from geolume_worker.metrics import PhaseTimer, current_rss_mb, peak_rss_mb
from geolume_worker.prancha import caminho_logo, conferir_logo_normalizada, validar_prancha
from geolume_worker.processing import process
from geolume_worker.qgis_session import is_qgis_initialized

VERSAO_ESQUEMA = 1
FASES = ("carregar", "processar", "renderizar_pdf", "total_job")


@dataclass
class JobResult:
    job_id: str
    pdf_path: Path
    json_path: Path
    memorial_path: Path
    phases_ms: dict[str, float]
    peak_rss_mb: float
    rss_before_mb: float
    rss_after_mb: float


def run_job(
    input_path: Path, output_dir: Path, job_id: str | None = None, prancha: dict | None = None
) -> JobResult:
    """`prancha` só muda o mapa.pdf; sem ela (jobs antigos) a prancha é a padrão."""
    if not is_qgis_initialized():
        raise RuntimeError("sessao QGIS nao inicializada")
    input_path = Path(input_path)
    job_id = job_id or uuid.uuid4().hex
    opcoes = validar_prancha(prancha)  # revalida: a fila não é confiável
    logo = None
    if opcoes.logo:
        logo = caminho_logo(output_dir, job_id)
        conferir_logo_normalizada(logo)
    job_dir = Path(output_dir) / job_id
    pdf_path = job_dir / "mapa.pdf"
    json_path = job_dir / "resultado.json"
    memorial_path = job_dir / "memorial.pdf"

    rss_before = current_rss_mb()
    timer = PhaseTimer()
    with timer.phase("total_job"):
        with timer.phase("carregar"):
            loaded = load_input(input_path)
        with timer.phase("processar"):
            summary = process(loaded)
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            with timer.phase("renderizar_pdf"):
                export_map_pdf(summary, pdf_path, prancha=opcoes, logo=logo)
                export_memorial_pdf(summary, memorial_path)
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
    phases_ms = {fase: timer.phases_ms[fase] for fase in FASES}

    result = JobResult(
        job_id=job_id,
        pdf_path=pdf_path,
        json_path=json_path,
        memorial_path=memorial_path,
        phases_ms=phases_ms,
        peak_rss_mb=round(peak_rss_mb(), 1),
        rss_before_mb=round(rss_before, 1),
        rss_after_mb=round(current_rss_mb(), 1),
    )
    resultado = {
        "versao_esquema": VERSAO_ESQUEMA,
        "job_id": job_id,
        "entrada": input_path.name,
        "propriedades": summary.properties,
        "pdf_memorial": str(memorial_path.name),
        "crs_saida": f"EPSG:{summary.epsg}",
        "area_ha": summary.area_ha,
        "perimetro_m": summary.perimetro_m,
        "vertices": [asdict(v) for v in summary.vertices],
        "metricas": {
            "fases_ms": phases_ms,
            "pico_rss_mb": result.peak_rss_mb,
            "rss_antes_mb": result.rss_before_mb,
            "rss_depois_mb": result.rss_after_mb,
        },
    }
    json_path.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

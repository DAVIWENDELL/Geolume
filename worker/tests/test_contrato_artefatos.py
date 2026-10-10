"""Golden do memorial.pdf e contrato do resultado.json (gleba fictícia de 6 vértices).

Comparam só o que é estável: texto do memorial (pdftotext -layout, sem data nem caminho) e o
resultado.json sem `job_id` (conferido à parte) e sem os valores de `metricas` (tempo e memória
variam; só a forma é conferida). Mudou de propósito? Regenere a referência e revise o diff.
"""

import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

import pdf_verif  # noqa: E402

from geolume_worker.job import run_job  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
ENTRADA = "gleba_rural_exemplo.geojson"
MEMORIAL = FIXTURES / "memorial_gleba_6v.txt"
RESULTADO = FIXTURES / "resultado_gleba_6v.json"

CHAVES = {"versao_esquema", "job_id", "entrada", "propriedades", "pdf_memorial", "crs_saida", "area_ha",
          "perimetro_m", "vertices", "metricas"}
CHAVES_VERTICE = {"id": str, "e": float, "n": float, "azimute": str, "distancia_m": float}
CHAVES_METRICAS = {"fases_ms", "pico_rss_mb", "rss_antes_mb", "rss_depois_mb"}
# Literais, não importados de job.py: o contrato não pode mudar junto com o código que ele confere.
VERSAO_ESQUEMA = 1
FASES = ("carregar", "processar", "renderizar_pdf", "total_job")

PRANCHAS = {
    "projeto-responsavel-legenda-lateral": {"projeto": "Loteamento Sol", "responsavel": "Eng. Ana",
                                            "legenda": "lateral"},
    "tecnico-alfa-legenda-inferior": {"estilo": "tecnico", "alfa_preenchimento": 0.35, "legenda": "inferior"},
    "pb-cores-e-logo": {"estilo": "pb", "cor_contorno": "#000000", "cor_preenchimento": "#FFFFFF", "logo": True},
}


def _numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def conferir_contrato(dados: object, job_id: str) -> list[str]:
    """Problemas do resultado.json frente ao contrato atual (versão 1); lista vazia = conforme."""
    if not isinstance(dados, dict):
        return ["raiz"]
    problemas = []
    if set(dados) != CHAVES:
        problemas.append(f"chaves:{sorted(set(dados) ^ CHAVES)}")
    esperados = {
        "versao_esquema": type(dados.get("versao_esquema")) is int and dados.get("versao_esquema") == VERSAO_ESQUEMA,
        "job_id": dados.get("job_id") == job_id,
        "entrada": dados.get("entrada") == ENTRADA,
        "propriedades": type(dados.get("propriedades")) is dict,
        "pdf_memorial": dados.get("pdf_memorial") == "memorial.pdf",
        "crs_saida": isinstance(dados.get("crs_saida"), str) and dados["crs_saida"].startswith("EPSG:")
        and dados["crs_saida"][5:].isdigit(),
        "area_ha": type(dados.get("area_ha")) is float,
        "perimetro_m": type(dados.get("perimetro_m")) is float,
        "vertices": type(dados.get("vertices")) is list and len(dados["vertices"]) >= 3
        and all(type(v) is dict and set(v) == set(CHAVES_VERTICE)
                and all(type(v[k]) is tipo for k, tipo in CHAVES_VERTICE.items()) for v in dados["vertices"]),
    }
    problemas += [campo for campo, ok in esperados.items() if campo in dados and not ok]
    metricas = dados.get("metricas")
    if "metricas" in dados and not (
        type(metricas) is dict and set(metricas) == CHAVES_METRICAS
        and type(metricas["fases_ms"]) is dict and list(metricas["fases_ms"]) == list(FASES)
        and all(_numero(v) for v in metricas["fases_ms"].values())
        and all(_numero(metricas[k]) for k in CHAVES_METRICAS - {"fases_ms"})
    ):
        problemas.append("metricas")
    return problemas


def estavel(dados: dict) -> dict:
    """resultado.json sem o que varia legitimamente entre execuções (job_id e valores de metricas)."""
    return {k: v for k, v in dados.items() if k not in {"job_id", "metricas"}}


def _gravar_logo(saida: Path, job_id: str) -> None:
    import io

    from PIL import Image

    from geolume_worker.prancha import caminho_logo, normalizar_logo

    buffer = io.BytesIO()
    Image.new("RGB", (300, 100), (20, 90, 160)).save(buffer, "PNG")
    destino = caminho_logo(saida, job_id)
    destino.parent.mkdir(parents=True, exist_ok=True)
    normalizar_logo(buffer.getvalue(), destino)


@pytest.fixture(scope="module")
def gerado(qgis_app, fixtures_dir, tmp_path_factory):
    """Um job sem prancha (o padrão) gerado de verdade pelo QGIS, numa pasta temporária."""
    saida = tmp_path_factory.mktemp("saida")
    resultado = run_job(fixtures_dir / ENTRADA, saida, job_id="golden")
    return resultado, json.loads(resultado.json_path.read_text(encoding="utf-8"))


def test_memorial_igual_a_referencia(gerado):
    resultado, _ = gerado
    assert pdf_verif.texto(resultado.memorial_path, "-layout") == MEMORIAL.read_text(encoding="utf-8")


def test_memorial_tem_as_secoes_publicas_na_ordem(gerado):
    resultado, _ = gerado
    texto = pdf_verif.texto(resultado.memorial_path)
    secoes = ["GeoLume — Memorial Descritivo Preliminar", "Imóvel:", "Proprietário:", "Município/UF:", "Matrícula:",
              "Área:", "Sistema de referência: SIRGAS 2000 / UTM — EPSG:", "Quadro de vértices", "Vértice",
              "Descrição perimetral", "Documento preliminar gerado automaticamente pelo Geolume."]
    posicoes = [texto.find(secao) for secao in secoes]
    assert -1 not in posicoes, [s for s, p in zip(secoes, posicoes) if p == -1]
    assert posicoes[:-1] == sorted(posicoes[:-1])  # o rodapé é desenhado antes, fora da ordem de leitura
    assert pdf_verif.paginas(resultado.memorial_path) == 1


def test_resultado_cumpre_o_contrato(gerado):
    _, dados = gerado
    assert conferir_contrato(dados, "golden") == []


def test_resultado_igual_a_referencia(gerado):
    _, dados = gerado
    assert estavel(dados) == json.loads(RESULTADO.read_text(encoding="utf-8"))


def test_ordem_das_chaves_do_resultado(gerado):
    _, dados = gerado
    assert list(dados) == ["versao_esquema", "job_id", "entrada", "propriedades", "pdf_memorial", "crs_saida",
                           "area_ha", "perimetro_m", "vertices", "metricas"]
    assert list(dados["vertices"][0]) == list(CHAVES_VERTICE)


@pytest.mark.parametrize("prancha", PRANCHAS.values(), ids=PRANCHAS.keys())
def test_prancha_nao_muda_memorial_nem_resultado(qgis_app, fixtures_dir, tmp_path, prancha):
    """Estilo, cores, opacidade, legenda, projeto, responsável e logo são só do mapa (coberto em test_layout_prancha)."""
    if prancha.get("logo"):
        _gravar_logo(tmp_path, "prancha")
    resultado = run_job(fixtures_dir / ENTRADA, tmp_path, job_id="prancha", prancha=prancha)
    dados = json.loads(resultado.json_path.read_text(encoding="utf-8"))
    assert pdf_verif.texto(resultado.memorial_path, "-layout") == MEMORIAL.read_text(encoding="utf-8")
    assert estavel(dados) == json.loads(RESULTADO.read_text(encoding="utf-8"))
    assert conferir_contrato(dados, "prancha") == []


# ---- O contrato recusa chave ausente, chave a mais e tipo trocado ---------------------------------------


@pytest.mark.parametrize("chave", sorted(CHAVES))
def test_contrato_recusa_chave_ausente(gerado, chave):
    _, dados = gerado
    alterado = copy.deepcopy(dados)
    del alterado[chave]
    assert conferir_contrato(alterado, "golden")


@pytest.mark.parametrize("chave, valor", [
    ("versao_esquema", "1"), ("versao_esquema", True), ("versao_esquema", 2), ("job_id", 1), ("entrada", None),
    ("propriedades", []), ("pdf_memorial", "mapa.pdf"), ("crs_saida", 31983), ("crs_saida", "31983"),
    ("area_ha", "12.3"), ("area_ha", None), ("area_ha", True), ("perimetro_m", 12), ("vertices", {}),
    ("vertices", []), ("metricas", []),
])
def test_contrato_recusa_tipo_ou_valor_trocado(gerado, chave, valor):
    _, dados = gerado
    alterado = copy.deepcopy(dados)
    alterado[chave] = valor
    assert conferir_contrato(alterado, "golden") == [chave]


@pytest.mark.parametrize("campo, valor", [("id", 1), ("e", "1.0"), ("n", None), ("azimute", 10.5),
                                          ("distancia_m", 3), (None, None)])
def test_contrato_recusa_vertice_alterado(gerado, campo, valor):
    _, dados = gerado
    alterado = copy.deepcopy(dados)
    if campo is None:
        alterado["vertices"][0]["extra"] = 1
    else:
        alterado["vertices"][0][campo] = valor
    assert conferir_contrato(alterado, "golden") == ["vertices"]


@pytest.mark.parametrize("mudar", ["sem_fase", "fase_a_mais", "fase_texto", "sem_pico", "pico_bool", "extra"])
def test_contrato_recusa_metricas_alteradas(gerado, mudar):
    _, dados = gerado
    alterado = copy.deepcopy(dados)
    metricas = alterado["metricas"]
    if mudar == "sem_fase":
        del metricas["fases_ms"]["processar"]
    elif mudar == "fase_a_mais":
        metricas["fases_ms"]["extra"] = 1.0
    elif mudar == "fase_texto":
        metricas["fases_ms"]["total_job"] = "1.0"
    elif mudar == "sem_pico":
        del metricas["pico_rss_mb"]
    elif mudar == "pico_bool":
        metricas["pico_rss_mb"] = True
    else:
        metricas["extra"] = 0
    assert conferir_contrato(alterado, "golden") == ["metricas"]


def test_contrato_recusa_chave_a_mais(gerado):
    _, dados = gerado
    assert conferir_contrato({**dados, "extra": 1}, "golden") == ["chaves:['extra']"]

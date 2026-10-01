"""Catálogo de camadas, estilos do polígono e regra única do alfa do preenchimento."""

import json
import re

import pytest

from geolume_worker.camadas import (
    ALFA_PADRAO,
    CAMADAS,
    ESTILO_PADRAO,
    ESTILOS,
    alfa8,
    catalogo_publico,
    estilo_por_id,
    validar_alfa,
)
from geolume_worker.errors import InvalidInputError

MENSAGEM_ALFA = "Opacidade do preenchimento deve estar entre 0 e 1."
CAMPOS = {"id", "nome", "tipo", "grupo", "situacao", "fonte", "url", "max_zoom",
          "exporta_pdf", "visivel", "ordem", "aviso"}

# ---- Contrato -----------------------------------------------------------------


def test_ids_unicos_e_validos():
    ids = [c.id for c in CAMADAS]
    assert len(ids) == len(set(ids)) == 9
    assert all(re.fullmatch(r"[a-z0-9_]{1,32}", i) for i in ids)


def test_nao_disponivel_sem_url_sem_pdf_invisivel():
    for camada in CAMADAS:
        if camada.situacao != "disponivel":
            assert camada.url is None and not camada.exporta_pdf and not camada.visivel, camada.id


def test_url_so_em_raster_disponivel_com_atribuicao_https():
    com_url = [c for c in CAMADAS if c.url]
    assert [c.id for c in com_url] == ["ruas_osm"]
    for camada in com_url:
        assert camada.situacao == "disponivel" and camada.tipo == "raster"
        assert camada.url.startswith("https://")
        assert camada.fonte.atribuicao.texto and camada.fonte.atribuicao.url.startswith("https://")


def test_exporta_pdf_so_vetor_sem_url():
    exportadas = [c for c in CAMADAS if c.exporta_pdf]
    assert [c.id for c in exportadas] == ["poligono"]
    assert all(c.tipo == "vetor" and c.url is None for c in exportadas)


def test_poligono_acima_de_todo_mapa_base():
    poligono = next(c for c in CAMADAS if c.id == "poligono")
    assert poligono.ordem == 100
    assert all(poligono.ordem > c.ordem for c in CAMADAS if c.grupo == "base")


def test_satelite_e_fontes_oficiais_desabilitados():
    por_id = {c.id: c for c in CAMADAS}
    for i in ("satelite", "limites_municipais", "hidrografia", "rodovias"):
        assert por_id[i].situacao == "depende_fonte_oficial" and por_id[i].fonte is None
    for i in ("topografia", "edificacoes"):
        assert por_id[i].situacao == "planejada" and por_id[i].fonte is None


def test_ruas_osm_aviso_e_atribuicao():
    ruas = next(c for c in CAMADAS if c.id == "ruas_osm")
    assert ruas.aviso == "Somente na pré-visualização — não entra no PDF"
    assert ruas.fonte.atribuicao.texto == "© Contribuidores do OpenStreetMap"
    assert ruas.fonte.atribuicao.url == "https://www.openstreetmap.org/copyright"
    assert ruas.fonte.licenca == "ODbL" and not ruas.exporta_pdf


def test_atribuicao_sem_html():
    texto = json.dumps(catalogo_publico(), ensure_ascii=False)
    assert not any(c in texto for c in "<>&")


def test_catalogo_publico_serializa_em_json():
    publico = json.loads(json.dumps(catalogo_publico()))
    assert set(publico) == {"camadas", "estilos", "alfa_padrao"}
    assert publico["alfa_padrao"] == 0.35
    assert all(set(c) == CAMPOS for c in publico["camadas"])
    assert [c["id"] for c in publico["camadas"]] == [c.id for c in CAMADAS]
    ruas = publico["camadas"][0]
    assert ruas["fonte"]["atribuicao"] == {"texto": "© Contribuidores do OpenStreetMap",
                                           "url": "https://www.openstreetmap.org/copyright"}


# ---- Estilos ------------------------------------------------------------------


def test_estilos_valores_da_spec():
    assert ESTILO_PADRAO == "padrao"
    assert [(e.id, e.nome, e.contorno, e.preenchimento, e.espessura_mm, e.espessura_px) for e in ESTILOS] == [
        ("padrao", "Padrão", "#C80000", "#FFC800", 0.6, 3),
        ("tecnico", "Técnico", "#1F2937", "#9CA3AF", 0.35, 2),
        ("pb", "Preto e branco", "#000000", "#FFFFFF", 0.5, 2),
    ]
    assert catalogo_publico()["estilos"][1] == {"id": "tecnico", "nome": "Técnico", "contorno": "#1F2937",
                                                 "preenchimento": "#9CA3AF", "espessura_mm": 0.35, "espessura_px": 2}


def test_estilo_desconhecido_keyerror():
    assert estilo_por_id("pb").nome == "Preto e branco"
    with pytest.raises(KeyError):
        estilo_por_id("urbano")


# ---- Alfa ---------------------------------------------------------------------


@pytest.mark.parametrize("valor, esperado", [(0, 0.0), (0.0, 0.0), (0.35, 0.35), (1, 1.0), ("0.5", 0.5), (" 1 ", 1.0)])
def test_alfa_aceito(valor, esperado):
    assert validar_alfa(valor) == esperado


@pytest.mark.parametrize("valor", [None, "", "  "])
def test_alfa_ausente_e_padrao(valor):
    assert validar_alfa(valor) == ALFA_PADRAO == 0.35


@pytest.mark.parametrize("valor", [-0.01, 1.01, "abc", "0,5", "NaN", "Infinity", "1e400", float("nan"),
                                   float("inf"), True, False, [], {}])
def test_alfa_recusado(valor):
    with pytest.raises(InvalidInputError) as erro:
        validar_alfa(valor)
    assert erro.value.codigo == "alfa_invalido"
    assert erro.value.mensagem == MENSAGEM_ALFA


def test_alfa8():
    assert (alfa8(0.35), alfa8(0), alfa8(1)) == (89, 0, 255)


# ---- Tabela compartilhada com o navegador (tests-web/unit/prancha.test.mjs) --------------

from pathlib import Path  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CASOS = json.loads((FIXTURES / "alfa_casos.json").read_text(encoding="utf-8"))
_ESPECIAIS = {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}


def _valor(caso):
    if isinstance(caso, dict) and set(caso) == {"especial"}:
        return _ESPECIAIS[caso["especial"]]
    return caso


def test_tabela_compartilhada_usa_a_mensagem_e_o_padrao_daqui():
    assert (CASOS["mensagem"], CASOS["padrao"]) == (MENSAGEM_ALFA, ALFA_PADRAO)


@pytest.mark.parametrize("valor, esperado", CASOS["aceitos"], ids=repr)
def test_tabela_alfa_aceito(valor, esperado):
    assert validar_alfa(valor) == esperado


@pytest.mark.parametrize("valor", CASOS["ausentes"], ids=repr)
def test_tabela_alfa_ausente(valor):
    assert validar_alfa(valor) == ALFA_PADRAO


@pytest.mark.parametrize("caso", CASOS["recusados"], ids=repr)
def test_tabela_alfa_recusado(caso):
    with pytest.raises(InvalidInputError) as erro:
        validar_alfa(_valor(caso))
    assert (erro.value.codigo, erro.value.mensagem) == ("alfa_invalido", MENSAGEM_ALFA)


@pytest.mark.parametrize("percentual, esperado", CASOS["alfa8"], ids=lambda v: str(v))
def test_tabela_alfa8_arredonda_metade_para_cima(percentual, esperado):
    # 30 % ⇒ 76,5 ⇒ 77 (o round() do Python daria 76 e o navegador, 77).
    assert alfa8(percentual / 100) == esperado


def test_catalogo_do_navegador_igual_ao_da_api():
    # tests-web usa este arquivo; se o catálogo mudar, regenere-o a partir de catalogo_publico().
    assert json.loads((FIXTURES / "catalogo_publico.json").read_text(encoding="utf-8")) == catalogo_publico()

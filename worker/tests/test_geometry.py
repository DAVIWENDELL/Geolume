import pytest

from geolume_worker.errors import InvalidInputError
from geolume_worker.geometry import (
    format_dms,
    grid_azimuth_deg,
    utm_epsg_for,
    validar_coordenadas,
    vertex_table,
)


def test_epsg_brasilia():
    assert utm_epsg_for(-47.9, -15.8) == 31983


def test_epsg_boa_vista_hemisferio_norte():
    assert utm_epsg_for(-60.7, 2.8) == 31975


def test_epsg_fora_da_cobertura():
    with pytest.raises(InvalidInputError) as exc:
        utm_epsg_for(10.0, 50.0)
    assert exc.value.codigo == "fora_da_cobertura"


@pytest.mark.parametrize(
    "destino, esperado",
    [((0, 10), 0), ((10, 0), 90), ((0, -10), 180), ((-10, 0), 270), ((10, 10), 45)],
)
def test_azimutes_cardeais(destino, esperado):
    assert grid_azimuth_deg(0, 0, *destino) == pytest.approx(esperado)


@pytest.mark.parametrize(
    "graus, texto",
    [
        (45.0, "45°00'00\""),
        (123.5125, "123°30'45\""),
        (29.99999999, "30°00'00\""),
        (359.9999999, "0°00'00\""),
    ],
)
def test_format_dms(graus, texto):
    assert format_dms(graus) == texto


def test_vertex_table_quadrado():
    vertices = vertex_table([(0, 0), (0, 10), (10, 10), (10, 0)])
    assert [v.id for v in vertices] == ["V1", "V2", "V3", "V4"]
    assert vertices[0].azimute == "0°00'00\""
    assert vertices[0].distancia_m == 10.0
    assert vertices[3].azimute == "270°00'00\""


def test_vertex_table_remove_duplicados_consecutivos():
    vertices = vertex_table([(0, 0), (0, 10), (0, 10), (10, 10), (10, 0), (0, 0)])
    assert len(vertices) == 4
    assert all(v.distancia_m > 0 for v in vertices)


# Regra real (_mesmo_ponto): E e N arredondados a 3 casas (round do Python) e comparados; não é distância < 1 mm.
E0, N0 = 189303.0, 8251045.0  # ordem de grandeza de E/N UTM reais (Brasília)


def _quantos(anel):
    return len(vertex_table(anel))


def test_vertex_table_mesmo_valor_arredondado_colapsa():
    # 0,4 mm: E arredonda para o mesmo milímetro do vizinho.
    assert _quantos([(E0, N0), (E0 + 0.0004, N0), (E0, N0 + 0.0004)]) == 1
    assert _quantos([(E0, N0), (E0 + 0.0004, N0), (E0 + 5, N0 + 5)]) == 2


def test_vertex_table_cruzar_a_fronteira_do_arredondamento_mantem_os_pontos():
    """0,2 mm entre si, mas de lados opostos da fronteira de 0,5 mm: arredondam diferente e ficam os 3."""
    assert _quantos([(E0 + 0.0004, N0), (E0 + 0.0006, N0), (E0 + 5, N0 + 5)]) == 3


def test_vertex_table_um_mm_exato_continua_distinto():
    assert _quantos([(E0, N0), (E0 + 0.001, N0), (E0, N0 + 0.001)]) == 3


def test_vertex_table_ponto_de_fechamento_igual_ao_primeiro_depois_do_arredondamento_sai():
    anel = [(E0, N0), (E0 + 10, N0), (E0 + 10, N0 + 10), (E0 + 0.0004, N0 + 0.0004)]
    vertices = vertex_table(anel)
    assert [(v.e, v.n) for v in vertices] == [(E0, N0), (E0 + 10, N0), (E0 + 10, N0 + 10)]


def test_vertex_table_so_compara_consecutivos():
    # O 3º volta ao milímetro do 1º, mas não é vizinho dele nem é o fechamento: fica.
    assert _quantos([(E0, N0), (E0 + 5, N0), (E0 + 0.0004, N0), (E0 + 5, N0 + 5)]) == 4


@pytest.mark.parametrize("sinal", [1, -1], ids=["positivo", "negativo"])
def test_vertex_table_arredondamento_igual_com_sinal_positivo_e_negativo(sinal):
    """-0,0004 e 0,0004 arredondam para -0.0 e 0.0, que são iguais; o resultado não depende do sinal."""
    assert _quantos([(sinal * 0.0004, sinal * 0.0004), (-sinal * 0.0004, 0.0), (sinal * 5, sinal * 5)]) == 2
    assert _quantos([(sinal * 0.0004, 0.0), (sinal * 0.0006, 0.0), (sinal * 5, sinal * 5)]) == 3


# ---- Coordenadas geográficas em GMS e fuso UTM --------------------------------

from geolume_worker.geometry import format_gms, fuso_utm  # noqa: E402


@pytest.mark.parametrize(
    "valor, eixo, texto",
    [
        (-(15 + 47 / 60 + 12.34 / 3600), "lat", "15°47'12.34\" S"),
        (2.8, "lat", "2°48'00.00\" N"),
        (-47.5, "lon", "47°30'00.00\" O"),
        (12.25, "lon", "12°15'00.00\" L"),
        (0.0, "lat", "0°00'00.00\" N"),
        (0.0, "lon", "0°00'00.00\" L"),
        (-0.000000001, "lat", "0°00'00.00\" N"),  # arredonda a zero: sem "S" para o Equador
        # 59,995" sobe para o minuto seguinte; nunca "60.00"
        (-(15 + 47 / 60 + 59.995 / 3600), "lat", "15°48'00.00\" S"),
        (-(15 + 47 / 60 + 59.994 / 3600), "lat", "15°47'59.99\" S"),
        (-(47 + 59 / 60 + 59.996 / 3600), "lon", "48°00'00.00\" O"),
        (-(59 + 59 / 60 + 59.995 / 3600), "lon", "60°00'00.00\" O"),
    ],
)
def test_format_gms(valor, eixo, texto):
    assert format_gms(valor, eixo) == texto


def test_format_gms_nunca_60_segundos_nem_60_minutos():
    for centesimos in range(0, 6000 * 2):
        valor = -(15 + 47 / 60 + (centesimos / 100 + 0.005) / 3600)
        texto = format_gms(valor, "lat")
        minutos, segundos = texto.split("°")[1].split("'")[:2]
        assert int(minutos) < 60 and float(segundos.rstrip('" S')) < 60, texto


def test_format_gms_eixo_invalido():
    with pytest.raises(ValueError):
        format_gms(1.0, "z")


@pytest.mark.parametrize(
    "epsg, esperado",
    [(31983, (23, "Sul")), (31978, (18, "Sul")), (31985, (25, "Sul")),
     (31975, (20, "Norte")), (31972, (17, "Norte")), (31977, (22, "Norte"))],
)
def test_fuso_utm(epsg, esperado):
    assert fuso_utm(epsg) == esperado


def test_fuso_utm_fora_da_cobertura():
    with pytest.raises(ValueError):
        fuso_utm(4674)


# ---- Escala numérica ------------------------------------------------------------

from geolume_worker.geometry import denominador_legivel, formatar_escala  # noqa: E402


@pytest.mark.parametrize(
    "calculado, legivel",
    [
        (173_412.7, 180_000),
        (180_000, 180_000),
        (180_000.0000001, 180_000),  # ruído de float não sobe o degrau
        (179_999.9999999, 180_000),
        (1_000, 1_000),
        (999.2, 1_000),
        (24_100, 25_000),
        (1_234_567, 1_300_000),
        (37.2, 38),
        (9.1, 10),
        (1, 1),
        (0.4, 1),
    ],
)
def test_denominador_legivel_arredonda_para_cima_com_dois_digitos(calculado, legivel):
    assert denominador_legivel(calculado) == legivel


def test_denominador_legivel_nunca_menor_que_o_calculado():
    for calculado in (12.3, 99.9, 101, 4_321, 98_765, 2_500_001):
        assert denominador_legivel(calculado) >= calculado


@pytest.mark.parametrize("invalido", [0, -5, float("nan"), float("inf")])
def test_denominador_legivel_invalido(invalido):
    with pytest.raises(ValueError):
        denominador_legivel(invalido)


@pytest.mark.parametrize(
    "denominador, texto",
    [(50, "1:50"), (1_000, "1:1.000"), (25_000, "1:25.000"), (180_000, "1:180.000"), (1_300_000, "1:1.300.000")],
)
def test_formatar_escala_com_ponto_de_milhar(denominador, texto):
    assert formatar_escala(denominador) == texto


# ---- Grade de coordenadas: GMS com casas variáveis e intervalo cartográfico ------

from geolume_worker.geometry import INTERVALOS_GRADE_S, intervalo_grade  # noqa: E402


@pytest.mark.parametrize(
    "valor, eixo, casas, texto",
    [
        (-15.75, "lat", 0, "15°45'00\" S"),
        (-47.925, "lon", 0, "47°55'30\" O"),
        (2.8, "lat", 1, "2°48'00.0\" N"),
        (-(15 + 44 / 60 + 59.6 / 3600), "lat", 0, "15°45'00\" S"),  # 59,6" sobe o minuto; nunca 60"
        (-(15 + 59 / 60 + 59.96 / 3600), "lat", 1, "16°00'00.0\" S"),
        (-(15 + 47 / 60 + 59.995 / 3600), "lat", 2, "15°48'00.00\" S"),
    ],
)
def test_format_gms_com_casas(valor, eixo, casas, texto):
    assert format_gms(valor, eixo, casas) == texto


def test_format_gms_padrao_continua_com_duas_casas():
    assert format_gms(-47.5, "lon") == format_gms(-47.5, "lon", 2) == "47°30'00.00\" O"


@pytest.mark.parametrize(
    "extensao_s, intervalo_s",
    [
        (0.08, 0.02),  # lote de ~2 m: centésimos de segundo
        (25, 5),  # gleba de ~700 m
        (31, 10),
        (300, 60),  # ~10 km: 1'
        (7920, 1800),  # ~2,2°: 30'
    ],
)
def test_intervalo_grade(extensao_s, intervalo_s):
    assert intervalo_grade(extensao_s / 3600) * 3600 == pytest.approx(intervalo_s)


def test_intervalo_grade_da_entre_2_e_6_intervalos():
    for extensao_s in (0.05, 0.3, 1.7, 12, 59, 420, 2_000, 9_000, 40_000):
        intervalo = intervalo_grade(extensao_s / 3600) * 3600
        assert intervalo in INTERVALOS_GRADE_S
        assert 2 <= extensao_s / intervalo <= 6 or intervalo == INTERVALOS_GRADE_S[0], (extensao_s, intervalo)


@pytest.mark.parametrize("invalido", [0, -1, float("nan")])
def test_intervalo_grade_invalido(invalido):
    with pytest.raises(ValueError):
        intervalo_grade(invalido)


# ---- Limites geográficos e cobertura SIRGAS 2000 / UTM por vértice ---------------------------------

_COBERTURA = "fusos 17N a 22N e 18S a 25S"


def _erro_coordenadas(anel):
    with pytest.raises(InvalidInputError) as exc:
        validar_coordenadas(anel)
    assert str(exc.value) == f"{exc.value.codigo}: {exc.value.mensagem}"
    return exc.value


def test_latitude_acima_de_90_graus_e_rejeitada():
    erro = _erro_coordenadas([(-47.9, -15.8), (-47.8, -15.8), (-47.8, 95.0)])
    assert erro.codigo == "coordenada_invalida"
    assert erro.mensagem == (
        "Latitude 95° no vértice 3 fora do intervalo de -90° a 90°. "
        "Confira se as coordenadas estão na ordem longitude, latitude."
    )


def test_latitude_abaixo_de_menos_90_graus_e_rejeitada():
    assert _erro_coordenadas([(-50.0, -90.5), (-49.9, -15.8), (-49.9, -15.7)]).codigo == "coordenada_invalida"


def test_longitude_acima_de_180_graus_e_rejeitada():
    erro = _erro_coordenadas([(200.0, -15.0), (200.01, -15.0), (200.01, -14.99)])
    assert erro.codigo == "coordenada_invalida"
    assert erro.mensagem == "Longitude 200° no vértice 1 fora do intervalo de -180° a 180°."


def test_longitude_abaixo_de_menos_180_graus_e_rejeitada():
    assert _erro_coordenadas([(-47.9, -15.8), (-180.25, -15.8), (-47.8, -15.7)]).codigo == "coordenada_invalida"


def test_vertice_fora_da_cobertura_e_rejeitado_mesmo_com_centro_dentro():
    # Centro no fuso 25S (coberto), mas o vértice 2 cai no fuso 27S (longitude -20).
    erro = _erro_coordenadas([(-47.9, -15.8), (-20.0, -15.8), (-20.0, -15.7), (-47.9, -15.7)])
    assert erro.codigo == "fora_da_cobertura"
    assert erro.mensagem == (
        f"O vértice 2 (longitude -20°, latitude -15.8°) está fora da cobertura SIRGAS 2000 / UTM aceita: {_COBERTURA}."
    )


def test_limites_geograficos_vem_antes_da_cobertura():
    # Vértice 1 fora da cobertura e vértice 2 com latitude impossível: aparece o erro mais grave.
    assert _erro_coordenadas([(10.0, 50.0), (-47.9, 91.0), (-47.8, -15.7)]).codigo == "coordenada_invalida"


@pytest.mark.parametrize("anel", [
    [(-47.9, -15.8), (-47.89, -15.8), (-47.89, -15.79), (-47.9, -15.79)],  # Brasília, 23S
    [(-48.1, -15.8), (-47.9, -15.8), (-47.9, -15.7)],  # atravessa 22S/23S
    [(-51.1, -0.1), (-51.0, -0.1), (-51.0, 0.1), (-51.1, 0.1)],  # atravessa o equador no fuso 22
    [(-60.7, 2.8), (-60.6, 2.8), (-60.6, 2.9)],  # Boa Vista, 20N
])
def test_coordenadas_validas_na_cobertura_passam(anel):
    validar_coordenadas(anel)


def test_centro_fora_da_cobertura_tem_mensagem_clara_sem_ruido_de_float():
    with pytest.raises(InvalidInputError) as exc:
        utm_epsg_for(10.004999999999999, 50.004999999999995)
    assert exc.value.mensagem == (
        "O centro do polígono (longitude 10.005°, latitude 50.005°) está fora da cobertura SIRGAS 2000 / UTM aceita: "
        f"{_COBERTURA}."
    )

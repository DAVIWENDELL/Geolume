"""Opções da prancha: validação dos campos e normalização da logo (só PNG/JPEG reais)."""

import io
import struct
import zlib

import pytest
from PIL import Image

from geolume_worker.errors import InvalidInputError
from geolume_worker.prancha import (
    LOGO_MAX_BYTES,
    LOGO_MAX_LADO,
    Prancha,
    normalizar_logo,
    validar_prancha,
)

# ---- Campos -----------------------------------------------------------------


def test_sem_opcoes_e_o_padrao_atual():
    assert validar_prancha(None) == Prancha()
    assert validar_prancha({}) == Prancha()
    padrao = Prancha()
    assert (padrao.projeto, padrao.responsavel, padrao.logo) == (None, None, False)
    assert (padrao.cor_contorno, padrao.cor_preenchimento, padrao.legenda) == ("#C80000", "#FFC800", "nenhuma")
    assert padrao.padrao


def test_textos_sao_aparados_e_vazio_vira_ausente():
    prancha = validar_prancha({"projeto": "  Loteamento Sol  ", "responsavel": " \t "})
    assert prancha.projeto == "Loteamento Sol"
    assert prancha.responsavel is None
    assert not prancha.padrao


def test_texto_com_100_caracteres_passa_e_101_nao():
    assert validar_prancha({"projeto": "a" * 100}).projeto == "a" * 100
    assert validar_prancha({"responsavel": "é" * 100}).responsavel == "é" * 100
    for campo in ("projeto", "responsavel"):
        with pytest.raises(InvalidInputError) as erro:
            validar_prancha({campo: "a" * 101})
        assert erro.value.codigo == f"{campo}_invalido"


@pytest.mark.parametrize("valor", ["a\nb", "a\rb", "a\x00b", "a\x1bb", "a\tb", "a\x7fb", "a\u202eb", "a\u2066b"])
def test_texto_com_caractere_de_controle_e_recusado(valor):
    with pytest.raises(InvalidInputError) as erro:
        validar_prancha({"projeto": valor})
    assert erro.value.codigo == "projeto_invalido"


@pytest.mark.parametrize("valor", [123, ["a"], {"a": 1}, True])
def test_texto_que_nao_e_string_e_recusado(valor):
    with pytest.raises(InvalidInputError):
        validar_prancha({"responsavel": valor})


def test_texto_com_expressao_qgis_e_aceito_como_texto():
    # Literal: quem garante que não é avaliado é texto_literal, no layout.
    assert validar_prancha({"projeto": "[% env('PATH') %]"}).projeto == "[% env('PATH') %]"


def test_cores_normalizadas_em_maiusculas():
    prancha = validar_prancha({"cor_contorno": "#1a2b3c", "cor_preenchimento": "#00ff7F"})
    assert (prancha.cor_contorno, prancha.cor_preenchimento) == ("#1A2B3C", "#00FF7F")


def test_cor_vazia_usa_o_padrao():
    prancha = validar_prancha({"cor_contorno": "", "cor_preenchimento": None})
    assert (prancha.cor_contorno, prancha.cor_preenchimento) == ("#C80000", "#FFC800")


@pytest.mark.parametrize("valor", ["red", "#FFF", "#GGGGGG", "C80000", "#C800001", "rgb(1,2,3)", " #C80000x", 0xC80000])
def test_cor_invalida_e_recusada(valor):
    with pytest.raises(InvalidInputError) as erro:
        validar_prancha({"cor_preenchimento": valor})
    assert erro.value.codigo == "cor_invalida"


MENSAGEM_LEGENDA = "Legenda deve ser 'nenhuma', 'lateral' ou 'inferior'."


@pytest.mark.parametrize("valor", ["nenhuma", "lateral", "inferior"])
def test_aceita_os_tres_layouts(valor):
    assert validar_prancha({"legenda": valor}).legenda == valor


def test_legenda_vazia_ou_ausente_e_padrao():
    assert validar_prancha({"legenda": ""}).legenda == "nenhuma"
    assert validar_prancha({"legenda": None}).legenda == "nenhuma"
    assert validar_prancha({"projeto": "X"}).legenda == "nenhuma"


@pytest.mark.parametrize("valor", ["topo", " lateral", "LATERAL", "superior", 1, [], "x" * 10_000],
                         ids=["topo", "espaco", "maiuscula", "superior", "numero", "lista", "gigante"])
def test_recusa_layout_desconhecido(valor):
    with pytest.raises(InvalidInputError) as erro:
        validar_prancha({"legenda": valor})
    assert erro.value.codigo == "legenda_invalida"
    assert erro.value.mensagem == MENSAGEM_LEGENDA  # fixa: nunca ecoa o valor recebido


def test_logo_so_como_booleano():
    assert validar_prancha({"logo": True}).logo is True
    with pytest.raises(InvalidInputError):
        validar_prancha({"logo": "/etc/passwd"})


def test_dicionario_ida_e_volta():
    prancha = validar_prancha({
        "projeto": "Sítio [1]", "responsavel": "Eng. Ana", "cor_contorno": "#112233",
        "cor_preenchimento": "#445566", "legenda": "lateral", "logo": True,
    })
    assert validar_prancha(prancha.como_dict()) == prancha
    assert set(prancha.como_dict()) == {"projeto", "responsavel", "cor_contorno", "cor_preenchimento", "legenda", "logo",
                                        "estilo", "alfa_preenchimento"}


def test_chave_desconhecida_e_ignorada():
    assert validar_prancha({"caminho": "/saida/x", "owner_id": "u"}) == Prancha()


def test_estilo_e_alfa_padrao_sem_opcoes():
    padrao = Prancha()
    assert (padrao.estilo, padrao.alfa_preenchimento) == ("padrao", 0.35)
    assert validar_prancha({"estilo": "", "alfa_preenchimento": ""}).padrao


def test_prancha_antiga_sem_chaves_novas():
    antiga = {"projeto": None, "responsavel": None, "cor_contorno": "#112233",
              "cor_preenchimento": "#445566", "legenda": "lateral", "logo": False}
    prancha = validar_prancha(antiga)
    assert (prancha.estilo, prancha.alfa_preenchimento) == ("padrao", 0.35)
    assert prancha.cor_contorno == "#112233"


@pytest.mark.parametrize("valor", ["padrao", "tecnico", "pb"])
def test_estilo_valido(valor):
    assert validar_prancha({"estilo": valor}).estilo == valor


def test_estilo_ausente_ou_vazio_e_padrao():
    assert validar_prancha({"estilo": None}).estilo == "padrao"
    assert validar_prancha({"estilo": ""}).estilo == "padrao"
    assert validar_prancha({"projeto": "X"}).estilo == "padrao"


@pytest.mark.parametrize("valor", ["urbano", "Tecnico", "personalizado", 1, True, ["pb"]])
def test_estilo_invalido(valor):
    with pytest.raises(InvalidInputError) as erro:
        validar_prancha({"estilo": valor})
    assert erro.value.codigo == "estilo_invalido"
    assert erro.value.mensagem == "Estilo do polígono inválido."


@pytest.mark.parametrize("valor, esperado", [(0, 0.0), ("0.6", 0.6), (1, 1.0), (None, 0.35)])
def test_alfa_aceito_na_prancha(valor, esperado):
    assert validar_prancha({"alfa_preenchimento": valor}).alfa_preenchimento == esperado


@pytest.mark.parametrize("valor", [-0.1, 1.5, "2", "abc", "NaN", float("nan"), True])
def test_alfa_invalido_recusado(valor):
    with pytest.raises(InvalidInputError) as erro:
        validar_prancha({"alfa_preenchimento": valor})
    assert erro.value.codigo == "alfa_invalido"


def test_alfa_texto_com_virgula_e_recusado():
    with pytest.raises(InvalidInputError) as erro:
        validar_prancha({"alfa_preenchimento": "0,5"})
    assert erro.value.codigo == "alfa_invalido"
    assert erro.value.mensagem == "Opacidade do preenchimento deve estar entre 0 e 1."


def test_como_dict_inclui_estilo_e_alfa():
    prancha = validar_prancha({"estilo": "tecnico", "alfa_preenchimento": "0.6"})
    dados = prancha.como_dict()
    assert (dados["estilo"], dados["alfa_preenchimento"]) == ("tecnico", 0.6)
    assert validar_prancha(dados) == prancha
    assert not prancha.padrao


# ---- Logo ---------------------------------------------------------------------


def _imagem(formato: str, tamanho=(40, 20), modo="RGB", **opcoes) -> bytes:
    buffer = io.BytesIO()
    Image.new(modo, tamanho, (10, 120, 200, 128)[: len(modo)]).save(buffer, formato, **opcoes)
    return buffer.getvalue()


def _png_com_cabecalho(largura: int, altura: int) -> bytes:
    """PNG cujo IHDR anuncia dimensões enormes; os dados nem precisam existir."""
    def bloco(tipo: bytes, dados: bytes) -> bytes:
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados))

    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", ihdr) + bloco(b"IDAT", zlib.compress(b"\x00" * 64)) + bloco(b"IEND", b"")


def _jpeg_com_cabecalho(largura: int, altura: int) -> bytes:
    dados = bytearray(_imagem("JPEG"))
    sof = dados.index(b"\xff\xc0")
    dados[sof + 5: sof + 9] = struct.pack(">HH", altura, largura)
    return bytes(dados)


def _abrir(caminho):
    with Image.open(caminho) as imagem:
        imagem.load()
        return imagem.format, imagem.size, imagem.mode, imagem.copy()


def test_png_pequeno_vira_png_do_mesmo_tamanho(tmp_path):
    destino = tmp_path / "logo.png"
    normalizar_logo(_imagem("PNG"), destino)
    formato, tamanho, _, _ = _abrir(destino)
    assert (formato, tamanho) == ("PNG", (40, 20))


def test_jpeg_grande_reduzido_para_1000_px_mantendo_proporcao(tmp_path):
    destino = tmp_path / "logo.png"
    normalizar_logo(_imagem("JPEG", (3000, 1500)), destino)
    assert _abrir(destino)[:2] == ("PNG", (1000, 500))


def test_imagem_alta_reduzida_pelo_lado_maior(tmp_path):
    destino = tmp_path / "logo.png"
    normalizar_logo(_imagem("PNG", (500, 2000)), destino)
    assert _abrir(destino)[1] == (250, LOGO_MAX_LADO)


def test_transparencia_do_png_e_preservada(tmp_path):
    destino = tmp_path / "logo.png"
    normalizar_logo(_imagem("PNG", modo="RGBA"), destino)
    _, _, modo, imagem = _abrir(destino)
    assert modo == "RGBA" and imagem.getpixel((0, 0))[3] == 128


def test_orientacao_exif_do_jpeg_e_aplicada(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # girar 90°
    destino = tmp_path / "logo.png"
    normalizar_logo(_imagem("JPEG", (40, 20), exif=exif), destino)
    assert _abrir(destino)[1] == (20, 40)


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><script>alert(1)</script></svg>'


@pytest.mark.parametrize("conteudo", [
    SVG,
    b"",
    b"isto nao e imagem",
    b"\x89PNG\r\n\x1a\n" + b"lixo" * 20,  # assinatura PNG falsa
    b"\xff\xd8\xff\xe0" + b"lixo" * 20,  # assinatura JPEG falsa
    _imagem("PNG")[:60],  # PNG truncado
], ids=["svg", "vazio", "texto", "png-falso", "jpeg-falso", "png-truncado"])
def test_conteudo_que_nao_e_png_ou_jpeg_valido_e_recusado(tmp_path, conteudo):
    destino = tmp_path / "logo.png"
    with pytest.raises(InvalidInputError) as erro:
        normalizar_logo(conteudo, destino)
    assert erro.value.codigo == "logo_invalida"
    assert not destino.exists()


@pytest.mark.parametrize("formato", ["GIF", "WEBP", "BMP", "TIFF"])
def test_outros_formatos_de_imagem_sao_recusados(tmp_path, formato):
    destino = tmp_path / "logo.png"
    with pytest.raises(InvalidInputError) as erro:
        normalizar_logo(_imagem(formato), destino)
    assert erro.value.codigo == "logo_invalida"
    assert not destino.exists()


def test_logo_acima_de_2_mb_e_recusada_sem_decodificar(tmp_path):
    destino = tmp_path / "logo.png"
    with pytest.raises(InvalidInputError) as erro:
        normalizar_logo(_imagem("PNG") + b"\x00" * LOGO_MAX_BYTES, destino)
    assert erro.value.codigo == "logo_grande"
    assert not destino.exists()


@pytest.mark.parametrize("conteudo", [
    _png_com_cabecalho(30000, 30000),
    _png_com_cabecalho(20000, 10),
    _jpeg_com_cabecalho(60000, 60000),
], ids=["png-30000", "png-largura-20000", "jpeg-60000"])
def test_dimensoes_perigosas_sao_recusadas_antes_de_decodificar(tmp_path, conteudo):
    destino = tmp_path / "logo.png"
    with pytest.raises(InvalidInputError) as erro:
        normalizar_logo(conteudo, destino)
    assert erro.value.codigo == "logo_dimensoes"
    assert not destino.exists()


# ---- Caminho da logo -----------------------------------------------------------


def test_caminho_da_logo_vem_so_do_job_id(tmp_path):
    from geolume_worker.prancha import caminho_logo

    assert caminho_logo(tmp_path, "0f3a9c") == tmp_path / "logos" / "0f3a9c.png"


@pytest.mark.parametrize("job_id", ["", "../x", "a/b", "a\\b", "a.b", "..", "a\x00b", "x" * 65])
def test_caminho_da_logo_recusa_job_id_estranho(tmp_path, job_id):
    from geolume_worker.prancha import caminho_logo

    with pytest.raises(ValueError):
        caminho_logo(tmp_path, job_id)


@pytest.mark.parametrize("valor", ["a b", "a b", "a‎b", "a‏b", "a؜b"])
@pytest.mark.parametrize("campo", ["projeto", "responsavel"])
def test_projeto_e_responsavel_rejeitam_separadores_e_todos_controles_bidi(campo, valor):
    with pytest.raises(InvalidInputError) as erro:
        validar_prancha({campo: valor})
    assert erro.value.codigo == f"{campo}_invalido"

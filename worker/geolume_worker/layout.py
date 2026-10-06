"""Layout de impressão A4 paisagem exportado para PDF."""

import math
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsBasicNumericFormat,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsFeature,
    QgsFillSymbol,
    QgsLayoutExporter,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsTextFormat,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor, QFont

from geolume_worker.camadas import alfa8, estilo_por_id
from geolume_worker.geometry import denominador_legivel, format_gms, formatar_escala, fuso_utm, intervalo_grade
from geolume_worker.medida import cortar_para_caber, linhas_que_cabem, medir
from geolume_worker.prancha import Prancha
from geolume_worker.processing import ParcelSummary
from geolume_worker.texto import informado, texto_literal

TITULO_PADRAO = "GeoLume — Mapa de Localização"
# Rosa dos ventos: SVG próprio versionado no pacote (sem rede). Fica na coluna direita, entre o logo e o resumo.
ROSA_DOS_VENTOS = Path(__file__).parent / "assets" / "rosa_dos_ventos.svg"
_ROSA_X, _ROSA_Y, _ROSA_LADO = 238.5, 30, 28
_FONTE_ROSA = 9
# Cabeçalho: título e responsável à esquerda, logo à direita, tudo acima do mapa (y = 25).
_CABECALHO_LARGURA = 222
_LOGO_POSICAO, _LOGO_TAMANHO = (237, 5), (50, 17)
_FONTE_MINIMA = 6
# Zonas da página (mm). Nada passa da margem de 5 mm: a tabela e o aviso param em LIMITE_INFERIOR.
MARGEM = 5
PAGINA = (297, 210)
LIMITE_INFERIOR = PAGINA[1] - MARGEM
COLUNA_DIREITA = (218, 287)
TABELA_X, TABELA_Y = 10, 160
FAIXA_INFERIOR = (117, 210, 153, 205)  # x0, x1, y0, y1: abaixo do mapa, à direita da tabela
QUADRO_X = FAIXA_INFERIOR[0] + 3  # quadro de coordenadas: alinhado com a legenda inferior
QUADRO_BASE = LIMITE_INFERIOR - 3  # folga de 3 mm acima do limite inferior
_FONTE_TABELA = 7
_FONTE_PROPRIEDADES = 8
_FONTE_QUADRO = 8
_FONTE_ESCALA = 9
_FONTE_GRADE = 7
# Mapa: 3 mm mais baixo que a faixa até y = 150 para os rótulos da grade caberem antes de "Tabela de vértices".
MAPA_X, MAPA_Y, MAPA_LARGURA, MAPA_ALTURA = 10, 25, 200, 122


def _formatar(label: QgsLayoutItemLabel, tamanho: float) -> None:
    formato = QgsTextFormat()
    formato.setFont(QFont("DejaVu Sans"))
    formato.setSize(tamanho)
    label.setTextFormat(formato)
    label.adjustSizeToText()


def _label(layout: QgsPrintLayout, texto: str, tamanho: float, x: float, y: float) -> QgsLayoutItemLabel:
    label = QgsLayoutItemLabel(layout)
    label.setText(texto_literal(texto))  # nunca avaliado como expressão QGIS
    _formatar(label, tamanho)
    label.attemptMove(QgsLayoutPoint(x, y))
    layout.addLayoutItem(label)
    return label


def _label_ajustado(
    layout: QgsPrintLayout, texto: str, tamanho: float, x: float, y: float, altura_max: float
) -> QgsLayoutItemLabel:
    """Rótulo do cabeçalho: diminui a fonte até caber; no limite, quebra nas palavras."""
    label = _label(layout, texto, tamanho, x, y)
    while label.sizeWithUnits().width() > _CABECALHO_LARGURA and tamanho > _FONTE_MINIMA:
        tamanho -= 1
        _formatar(label, tamanho)
    if label.sizeWithUnits().width() > _CABECALHO_LARGURA:
        label.attemptResize(QgsLayoutSize(_CABECALHO_LARGURA, altura_max))
    return label


def _formato_numerico_pt() -> QgsBasicNumericFormat:
    """Rótulos da escala gráfica em português: vírgula decimal e ponto de milhar."""
    formato = QgsBasicNumericFormat()
    formato.setDecimalSeparator(",")
    formato.setThousandsSeparator(".")
    formato.setShowThousandsSeparator(True)
    formato.setShowTrailingZeros(False)
    return formato


def _simbolo(prancha: Prancha) -> QgsFillSymbol:
    """Cores da prancha, espessura do estilo e alfa8 no preenchimento (o navegador usa alfa8 / 255)."""
    def rgb(cor: str) -> str:
        return ",".join(str(int(cor[i:i + 2], 16)) for i in (1, 3, 5))

    return QgsFillSymbol.createSimple({
        "color": f"{rgb(prancha.cor_preenchimento)},{alfa8(prancha.alfa_preenchimento)}",
        "outline_color": f"{rgb(prancha.cor_contorno)},255",  # contorno sempre opaco
        "outline_width": str(estilo_por_id(prancha.estilo).espessura_mm),
        "outline_width_unit": "MM",
    })


def _legenda(layout: QgsPrintLayout, prancha: Prancha, x: float, y: float) -> None:
    _label(layout, "Legenda", 9, x, y)
    amostra = QgsLayoutItemShape(layout)
    amostra.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
    amostra.setSymbol(_simbolo(prancha))
    amostra.attemptMove(QgsLayoutPoint(x, y + 7))
    amostra.attemptResize(QgsLayoutSize(8, 5))
    layout.addLayoutItem(amostra)
    _label(layout, "Limite do imóvel", 8, x + 11, y + 7)


def _logo(layout: QgsPrintLayout, logo: Path) -> None:
    figura = QgsLayoutItemPicture(layout)
    figura.setPicturePath(str(logo), Qgis.PictureFormat.Raster)
    figura.setResizeMode(QgsLayoutItemPicture.ResizeMode.Zoom)  # cabe no quadro sem distorcer
    figura.setPictureAnchor(QgsLayoutItem.ReferencePoint.UpperRight)
    figura.attemptMove(QgsLayoutPoint(*_LOGO_POSICAO))
    figura.attemptResize(QgsLayoutSize(*_LOGO_TAMANHO))
    layout.addLayoutItem(figura)


def _metadata_lines(properties: dict[str, object]) -> list[str]:
    labels = {
        "nome_imovel": "Imóvel",
        "proprietario": "Proprietário",
        "municipio": "Município",
        "uf": "UF",
        "matricula": "Matrícula",
        "tipo": "Tipo",
    }
    linhas = []
    for chave, rotulo in labels.items():
        valor = informado(properties.get(chave))
        if valor:
            linhas.append(f"{rotulo}: {valor}")
    return linhas


_CABECALHO_TABELA = [
    "Vértice        E (m)          N (m)        Azimute       Dist. (m)",
    "────────────────────────────────────────────────────────────────────",
]


def _linhas_tabela(summary: ParcelSummary) -> list[str]:
    return [
        f"{vertice.id:<8} {vertice.e:>12.2f} {vertice.n:>12.2f} "
        f"{vertice.azimute:>14} {vertice.distancia_m:>10.2f}"
        for vertice in summary.vertices
    ]


def _aviso_tabela(exibidas: int, total: int) -> str:
    return f"Exibidos {exibidas} de {total} vértices. Demais vértices no memorial descritivo."


def _tabela_vertices(layout: QgsPrintLayout, summary: ParcelSummary) -> None:
    """Só as linhas que cabem até LIMITE_INFERIOR; as demais ficam no memorial, com aviso."""
    linhas = _linhas_tabela(summary)
    altura = LIMITE_INFERIOR - TABELA_Y
    exibidas = linhas_que_cabem(layout, _CABECALHO_TABELA, linhas, _FONTE_TABELA, altura)
    aviso = None
    if exibidas < len(linhas):
        # O próprio aviso ocupa espaço: recalcula descontando a altura dele.
        altura -= medir(layout, _aviso_tabela(len(linhas), len(linhas)), _FONTE_TABELA)[1] + 1
        exibidas = linhas_que_cabem(layout, _CABECALHO_TABELA, linhas, _FONTE_TABELA, altura)
        aviso = _aviso_tabela(exibidas, len(linhas))
    tabela = _label(layout, "\n".join(_CABECALHO_TABELA + linhas[:exibidas]), _FONTE_TABELA, TABELA_X, TABELA_Y)
    if aviso:
        fim = tabela.positionWithUnits().y() + tabela.sizeWithUnits().height()
        _label(layout, aviso, _FONTE_TABELA, TABELA_X, fim + 1)


def _quadro_coordenadas(layout: QgsPrintLayout, summary: ParcelSummary) -> None:
    """SRC completo, centroide em GMS e UTM e a fonte da geometria, na base da faixa inferior."""
    fuso, hemisferio = fuso_utm(summary.epsg)
    lon, lat = summary.centroide_geo
    centro = summary.geometry_utm.centroid().asPoint()
    texto = "\n".join([
        f"SRC: SIRGAS 2000 / UTM fuso {fuso} {hemisferio} — EPSG:{summary.epsg}",
        "Latitude/longitude: SIRGAS 2000 geográficas — EPSG:4674",
        f"Lat {format_gms(lat, 'lat')}   Long {format_gms(lon, 'lon')}",
        f"UTM: E {centro.x():.2f} m   N {centro.y():.2f} m",
        # Quebrada em duas linhas: inteira, em 8 pt, passaria da largura da faixa (x ≤ 210).
        "Coordenadas apresentadas correspondem",
        "ao centroide da geometria.",
        # Só a origem real: o GeoJSON do usuário não é fonte oficial nem cadastral.
        "Fonte da geometria: GeoJSON fornecido pelo usuário.",
    ])
    quadro = _label(layout, texto, _FONTE_QUADRO, QUADRO_X, FAIXA_INFERIOR[2])
    quadro.attemptMove(QgsLayoutPoint(QUADRO_X, QUADRO_BASE - quadro.sizeWithUnits().height()))


def _rotulos_grade(layout: QgsPrintLayout, mapa: QgsLayoutItemMap, intervalo: float) -> list[tuple[str, float, str]]:
    """(eixo da grade, valor em graus, texto GMS) das linhas que cruzam a moldura com o rótulo inteiro dentro dela.

    Longitude no lado inferior, latitude no esquerdo; rótulo que passaria do canto é omitido (a cruzeta fica).
    """
    geo = QgsCoordinateReferenceSystem("EPSG:4674")
    contexto = QgsProject.instance().transformContext()
    para_geo = QgsCoordinateTransform(mapa.crs(), geo, contexto)
    para_mapa = QgsCoordinateTransform(geo, mapa.crs(), contexto)
    e = mapa.extent()
    cantos = [para_geo.transform(QgsPointXY(x, y)) for x in (e.xMinimum(), e.xMaximum())
              for y in (e.yMinimum(), e.yMaximum())]
    lat_inferior = para_geo.transform(QgsPointXY(e.center().x(), e.yMinimum())).y()
    lon_esquerda = para_geo.transform(QgsPointXY(e.xMinimum(), e.center().y())).x()
    passo_s = intervalo * 3600
    casas = 0 if passo_s >= 1 else 1 if passo_s >= 0.1 else 2
    rotulos = []
    for eixo, valores in (("lon", [c.x() for c in cantos]), ("lat", [c.y() for c in cantos])):
        primeiro = math.ceil(min(valores) / intervalo - 1e-9)
        ultimo = math.floor(max(valores) / intervalo + 1e-9)
        for k in range(primeiro, ultimo + 1):
            valor = k * passo_s / 3600  # múltiplo exato do intervalo, sem acumular erro
            texto = format_gms(valor, eixo, casas)
            if eixo == "lon":
                ponto = para_mapa.transform(QgsPointXY(valor, lat_inferior))
                posicao, lado = (ponto.x() - e.xMinimum()) / e.width() * MAPA_LARGURA, MAPA_LARGURA
            else:
                ponto = para_mapa.transform(QgsPointXY(lon_esquerda, valor))
                posicao, lado = (ponto.y() - e.yMinimum()) / e.height() * MAPA_ALTURA, MAPA_ALTURA
            meio = medir(layout, texto, _FONTE_GRADE)[0] / 2 + 1
            if meio <= posicao <= lado - meio:
                rotulos.append(("x" if eixo == "lon" else "y", valor, texto))
    return rotulos


def _grade_gms(layout: QgsPrintLayout, mapa: QgsLayoutItemMap) -> None:
    """Grade geográfica SIRGAS 2000 (EPSG:4674) sobre o mapa em UTM: cruzetas discretas e rótulos GMS na moldura.

    Os formatos GMS nativos do QGIS usam E/W; os rótulos vêm de format_gms (L/O) por uma expressão gerada aqui,
    só com textos calculados — nada do usuário entra nela.
    """
    e = mapa.extent()
    para_geo = QgsCoordinateTransform(mapa.crs(), QgsCoordinateReferenceSystem("EPSG:4674"),
                                      QgsProject.instance().transformContext())
    cantos = [para_geo.transform(QgsPointXY(x, y)) for x in (e.xMinimum(), e.xMaximum())
              for y in (e.yMinimum(), e.yMaximum())]
    extensao = max(max(c.x() for c in cantos) - min(c.x() for c in cantos),
                   max(c.y() for c in cantos) - min(c.y() for c in cantos))
    intervalo = intervalo_grade(extensao)
    casos = " ".join(
        f"WHEN @grid_axis = '{eixo}' AND abs(@grid_number - ({valor!r})) < {intervalo / 1000!r} "
        f"THEN {QgsExpression.quotedString(texto)}"
        for eixo, valor, texto in _rotulos_grade(layout, mapa, intervalo)
    )

    grade = QgsLayoutItemMapGrid("Coordenadas geográficas", mapa)
    grade.setCrs(QgsCoordinateReferenceSystem("EPSG:4674"))
    grade.setIntervalX(intervalo)
    grade.setIntervalY(intervalo)
    grade.setStyle(QgsLayoutItemMapGrid.GridStyle.Cross)
    grade.setCrossLength(1.5)
    grade.setGridLineWidth(0.15)
    grade.setGridLineColor(QColor(90, 90, 90))
    # Marcas curtas por dentro da moldura indicam onde cada linha da grade chega à borda.
    grade.setFrameStyle(QgsLayoutItemMapGrid.FrameStyle.InteriorTicks)
    grade.setFrameWidth(1.5)
    grade.setFramePenSize(0.15)
    grade.setFramePenColor(QColor(90, 90, 90))
    grade.setAnnotationEnabled(True)
    grade.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.CustomFormat)
    grade.setAnnotationExpression(f"CASE {casos} ELSE '' END" if casos else "''")
    formato = QgsTextFormat()
    formato.setFont(QFont("DejaVu Sans"))
    formato.setSize(_FONTE_GRADE)
    grade.setAnnotationTextFormat(formato)
    grade.setAnnotationFrameDistance(1)
    lado, modo = QgsLayoutItemMapGrid.BorderSide, QgsLayoutItemMapGrid.DisplayMode
    for borda, exibir in ((lado.Left, modo.LatitudeOnly), (lado.Bottom, modo.LongitudeOnly),
                          (lado.Top, modo.HideAll), (lado.Right, modo.HideAll)):
        grade.setAnnotationDisplay(exibir, borda)
        grade.setAnnotationPosition(QgsLayoutItemMapGrid.AnnotationPosition.OutsideMapFrame, borda)
    grade.setAnnotationDirection(QgsLayoutItemMapGrid.AnnotationDirection.Vertical, lado.Left)
    grade.setAnnotationDirection(QgsLayoutItemMapGrid.AnnotationDirection.Horizontal, lado.Bottom)
    mapa.grids().addGrid(grade)
    mapa.updateBoundingRect()


def _rosa_dos_ventos(layout: QgsPrintLayout, mapa: QgsLayoutItemMap) -> None:
    """Rosa dos ventos vinculada ao mapa, com N (negrito), S, L e O em volta.

    O mapa é sempre norte para cima, então as letras (rótulos fixos) ficam coerentes com o giro da figura.
    """
    rosa = QgsLayoutItemPicture(layout)
    rosa.setPicturePath(str(ROSA_DOS_VENTOS), Qgis.PictureFormat.SVG)
    rosa.setLinkedMap(mapa)
    rosa.attemptMove(QgsLayoutPoint(_ROSA_X, _ROSA_Y))
    rosa.attemptResize(QgsLayoutSize(_ROSA_LADO, _ROSA_LADO))
    layout.addLayoutItem(rosa)
    cx, cy = _ROSA_X + _ROSA_LADO / 2, _ROSA_Y + _ROSA_LADO / 2
    for letra in ("N", "S", "L", "O"):
        rotulo = _label(layout, letra, _FONTE_ROSA, 0, 0)
        if letra == "N":
            formato = rotulo.textFormat()
            fonte = formato.font()
            fonte.setBold(True)
            formato.setFont(fonte)
            rotulo.setTextFormat(formato)
            rotulo.adjustSizeToText()
        largura, altura = rotulo.sizeWithUnits().width(), rotulo.sizeWithUnits().height()
        x, y = {
            "N": (cx - largura / 2, _ROSA_Y - altura),
            "S": (cx - largura / 2, _ROSA_Y + _ROSA_LADO),
            "L": (_ROSA_X + _ROSA_LADO, cy - altura / 2),
            "O": (_ROSA_X - largura, cy - altura / 2),
        }[letra]
        rotulo.attemptMove(QgsLayoutPoint(x, y))


def montar_mapa(
    summary: ParcelSummary,
    title: str = TITULO_PADRAO,
    *,
    prancha: Prancha | None = None,
    logo: Path | None = None,
) -> tuple[QgsProject, QgsPrintLayout]:
    """Monta a prancha; o chamador guarda o projeto enquanto usar o layout."""
    prancha = prancha or Prancha()
    if prancha.projeto:
        title = f"{prancha.projeto} — Mapa de Localização"
    # Projeto próprio por chamada: nenhum estado compartilhado entre jobs via QgsProject.instance().
    project = QgsProject()
    layer = QgsVectorLayer(f"Polygon?crs=EPSG:{summary.epsg}", "Lote", "memory")
    feature = QgsFeature()
    feature.setGeometry(summary.geometry_utm)
    layer.dataProvider().addFeatures([feature])
    layer.updateExtents()
    layer.renderer().setSymbol(_simbolo(prancha))
    project.addMapLayer(layer)
    project.setCrs(layer.crs())

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize("A4", QgsLayoutItemPage.Orientation.Landscape)

    mapa = QgsLayoutItemMap(layout)
    mapa.attemptMove(QgsLayoutPoint(MAPA_X, MAPA_Y))
    mapa.attemptResize(QgsLayoutSize(MAPA_LARGURA, MAPA_ALTURA))
    mapa.setCrs(layer.crs())
    mapa.setLayers([layer])
    extent = summary.geometry_utm.boundingBox()
    extent.scale(1.2)
    mapa.zoomToExtent(extent)
    # Escala legível aplicada ao próprio mapa: o texto e a barra gráfica mostram a escala real.
    mapa.setScale(denominador_legivel(mapa.scale()))
    mapa.setFrameEnabled(True)
    layout.addLayoutItem(mapa)
    _grade_gms(layout, mapa)

    _label_ajustado(layout, title, 18, 10, 8, altura_max=8.5)
    if prancha.responsavel:
        _label_ajustado(layout, f"Responsável técnico: {prancha.responsavel}", 9, 10, 17, altura_max=7)
    if logo is not None:
        _logo(layout, logo)
    largura_coluna = COLUNA_DIREITA[1] - COLUNA_DIREITA[0]
    for indice, texto in enumerate(_metadata_lines(summary.properties)):
        # Valor longo do GeoJSON é cortado só aqui; inteiro no memorial e no resultado.json.
        texto = cortar_para_caber(layout, texto, _FONTE_PROPRIEDADES, largura_coluna)
        _label(layout, texto, _FONTE_PROPRIEDADES, COLUNA_DIREITA[0], 68 + indice * 8)
    _label(
        layout,
        f"SIRGAS 2000 / UTM — EPSG:{summary.epsg}\n"
        f"Área: {summary.area_ha:.4f} ha\n"
        f"Perímetro: {summary.perimetro_m:.2f} m\n"
        f"Vértices: {len(summary.vertices)}",
        10,
        218,
        120,
    )

    _rosa_dos_ventos(layout, mapa)

    escala = QgsLayoutItemScaleBar(layout)
    escala.setStyle("Single Box")
    escala.setLinkedMap(mapa)
    escala.applyDefaultSize()
    # FitWidth depois do tamanho padrão: segmentos com valores redondos e barra entre 30 e 60 mm.
    escala.setNumberOfSegments(4)
    escala.setNumberOfSegmentsLeft(0)
    escala.setSegmentSizeMode(Qgis.ScaleBarSegmentSizeMode.FitWidth)
    escala.setMinimumBarWidth(30)
    escala.setMaximumBarWidth(60)
    escala.setNumericFormat(_formato_numerico_pt())
    escala.update()
    # Em metros, rótulos de 4+ dígitos se fundem ("1.2001.600" em 1:32.000): a partir de 1 000 m de barra, km.
    if escala.unitsPerSegment() * escala.numberOfSegments() >= 1000:
        escala.setUnits(Qgis.DistanceUnit.Kilometers)
        escala.setUnitLabel("km")
        escala.update()
    escala.attemptMove(QgsLayoutPoint(218, 155))
    layout.addLayoutItem(escala)
    # Logo acima da barra, no vão entre o resumo (termina em y ≈ 137) e a escala gráfica.
    numerica = _label(layout, f"Escala numérica: {formatar_escala(round(mapa.scale()))}", _FONTE_ESCALA,
                      COLUNA_DIREITA[0], 0)
    numerica.attemptMove(QgsLayoutPoint(COLUNA_DIREITA[0], 153 - numerica.sizeWithUnits().height()))

    _label(layout, "Tabela de vértices", 9, 10, 153)
    _tabela_vertices(layout, summary)
    _quadro_coordenadas(layout, summary)
    if prancha.legenda == "lateral":
        _legenda(layout, prancha, COLUNA_DIREITA[0], escala.positionWithUnits().y() + escala.sizeWithUnits().height() + 5)
    elif prancha.legenda == "inferior":
        # Faixa abaixo do mapa, à direita da tabela (que nunca passa de x = 112).
        _legenda(layout, prancha, FAIXA_INFERIOR[0] + 3, FAIXA_INFERIOR[2] + 2)
    return project, layout


def export_map_pdf(
    summary: ParcelSummary,
    output_path: Path,
    title: str = TITULO_PADRAO,
    *,
    prancha: Prancha | None = None,
    logo: Path | None = None,
) -> Path:
    project, layout = montar_mapa(summary, title, prancha=prancha, logo=logo)  # noqa: F841 — mantém o layout vivo
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = 300
    # Texto como texto (não curvas): PDF pesquisável e verificável com pdftotext.
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysText
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resultado = QgsLayoutExporter(layout).exportToPdf(str(output_path), settings)
    if resultado != QgsLayoutExporter.ExportResult.Success:
        raise RuntimeError(f"Falha ao exportar PDF: {resultado}")
    return output_path

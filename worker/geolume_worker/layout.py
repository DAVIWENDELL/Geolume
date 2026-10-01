"""Layout de impressão A4 paisagem exportado para PDF."""

from pathlib import Path

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFillSymbol,
    QgsLayoutExporter,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPathResolver,
    QgsPrintLayout,
    QgsProject,
    QgsSymbolLayerUtils,
    QgsTextFormat,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QFont

from geolume_worker.camadas import alfa8, estilo_por_id
from geolume_worker.medida import cortar_para_caber, linhas_que_cabem, medir
from geolume_worker.prancha import Prancha
from geolume_worker.processing import ParcelSummary
from geolume_worker.texto import informado, texto_literal

TITULO_PADRAO = "GeoLume — Mapa de Localização"
_SETA_NORTE = "arrows/NorthArrow_02.svg"
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
_FONTE_TABELA = 7
_FONTE_PROPRIEDADES = 8


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
    mapa.attemptMove(QgsLayoutPoint(10, 25))
    mapa.attemptResize(QgsLayoutSize(200, 125))
    mapa.setCrs(layer.crs())
    mapa.setLayers([layer])
    extent = summary.geometry_utm.boundingBox()
    extent.scale(1.2)
    mapa.zoomToExtent(extent)
    mapa.setFrameEnabled(True)
    layout.addLayoutItem(mapa)

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

    seta = QgsLayoutItemPicture(layout)
    seta.setPicturePath(QgsSymbolLayerUtils.svgSymbolNameToPath(_SETA_NORTE, QgsPathResolver()))
    seta.setLinkedMap(mapa)
    seta.attemptMove(QgsLayoutPoint(240, 25))
    seta.attemptResize(QgsLayoutSize(20, 25))
    layout.addLayoutItem(seta)

    escala = QgsLayoutItemScaleBar(layout)
    escala.setStyle("Single Box")
    escala.setLinkedMap(mapa)
    escala.applyDefaultSize()
    # FitWidth depois do tamanho padrão: mantém a barra legível de lotes de 1 m² a 100 000 ha.
    escala.setNumberOfSegments(4)
    escala.setNumberOfSegmentsLeft(0)
    escala.setSegmentSizeMode(Qgis.ScaleBarSegmentSizeMode.FitWidth)
    escala.setMinimumBarWidth(30)
    escala.setMaximumBarWidth(60)
    escala.update()
    escala.attemptMove(QgsLayoutPoint(218, 155))
    layout.addLayoutItem(escala)

    _label(layout, "Tabela de vértices", 9, 10, 153)
    _tabela_vertices(layout, summary)
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

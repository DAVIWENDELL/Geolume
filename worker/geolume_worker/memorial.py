"""Geração de um memorial descritivo técnico preliminar em PDF."""

from pathlib import Path

from qgis.core import (
    Qgis,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemPage,
    QgsLayoutPoint,
    QgsPrintLayout,
    QgsProject,
    QgsTextFormat,
)
from qgis.PyQt.QtGui import QFont

from geolume_worker.medida import linhas_que_cabem, quebrar_por_largura
from geolume_worker.processing import ParcelSummary
from geolume_worker.texto import informado, texto_literal

# Área útil do A4 retrato (mm). Posições mínimas = as de antes: memoriais pequenos não mudam.
X = 18
LARGURA = 192 - X
TOPO = 15
BASE = 260
RODAPE_Y = 265
RODAPE = "Documento preliminar gerado automaticamente pelo Geolume."
TITULO_SECAO = 10  # distância do título de seção ao conteúdo
_CABECALHO_TABELA = ["Vértice    Este (m)       Norte (m)        Azimute        Distância (m)"]


def _label(
    layout: QgsPrintLayout, texto: str, tamanho: float, x: float, y: float, pagina: int = 0
) -> QgsLayoutItemLabel:
    label = QgsLayoutItemLabel(layout)
    label.setText(texto_literal(texto))  # nunca avaliado como expressão QGIS
    formato = QgsTextFormat()
    formato.setFont(QFont("DejaVu Sans"))
    formato.setSize(tamanho)
    label.setTextFormat(formato)
    label.adjustSizeToText()
    label.attemptMove(QgsLayoutPoint(x, y), page=pagina)
    layout.addLayoutItem(label)
    return label


def _fim(label: QgsLayoutItemLabel) -> float:
    return label.pagePositionWithUnits().y() + label.sizeWithUnits().height()


def _aparar(linhas: list[str]) -> list[str]:
    """Sem linhas em branco nas pontas: nenhuma página começa ou termina com um vazio."""
    inicio, fim = 0, len(linhas)
    while inicio < fim and not linhas[inicio]:
        inicio += 1
    while fim > inicio and not linhas[fim - 1]:
        fim -= 1
    return linhas[inicio:fim]


class _Paginador:
    """Empilha blocos na área útil (15–260 mm) e abre página A4 nova quando o bloco não cabe.

    Toda página recebe o rodapé; um título de seção só é colocado junto com ao menos uma
    linha do seu conteúdo, então nunca fica órfão no fim da página.
    """

    def __init__(self, layout: QgsPrintLayout):
        self.layout = layout
        self.pagina = 0
        _label(layout, RODAPE, 8, X, RODAPE_Y)

    def _nova_pagina(self) -> None:
        pagina = QgsLayoutItemPage(self.layout)
        pagina.setPageSize("A4", QgsLayoutItemPage.Orientation.Portrait)
        self.layout.pageCollection().addPage(pagina)
        self.pagina += 1
        _label(self.layout, RODAPE, 8, X, RODAPE_Y, self.pagina)

    def bloco(
        self,
        y: float,
        linhas: list[str],
        tamanho: float,
        titulo: str | None = None,
        titulo_continuacao: str | None = None,
        cabecalho: list[str] | None = None,
    ) -> float:
        """Coloca as linhas a partir de y, repartidas entre páginas; devolve o fim (mm) na página atual."""
        cabecalho = cabecalho or []
        linhas = _aparar(linhas)
        fim = y
        while linhas:
            conteudo_y = y + (TITULO_SECAO if titulo else 0)
            k = linhas_que_cabem(self.layout, cabecalho, linhas, tamanho, BASE - conteudo_y) if conteudo_y < BASE else 0
            if k == 0 and y > TOPO:
                self._nova_pagina()
                y = TOPO
                continue
            k = max(k, 1)  # uma linha mais alta que a página inteira não trava o laço
            if titulo:
                _label(self.layout, titulo, 12, X, y, self.pagina)
            fatia = cabecalho + _aparar(linhas[:k])
            fim = _fim(_label(self.layout, "\n".join(fatia), tamanho, X, conteudo_y, self.pagina))
            linhas = _aparar(linhas[k:])
            if linhas:
                self._nova_pagina()
                y = TOPO
                titulo = titulo_continuacao
        return fim


def _propriedades(summary: ParcelSummary) -> dict[str, object]:
    # Valores do GeoJSON em uma linha; nulo ou vazio vira None e recebe o texto padrão.
    return {chave: informado(valor) for chave, valor in summary.properties.items()}


def memorial_text(summary: ParcelSummary) -> str:
    propriedades = _propriedades(summary)
    nome = propriedades.get("nome_imovel") or "Imóvel não informado"
    municipio = propriedades.get("municipio") or "Município não informado"
    uf = propriedades.get("uf") or "UF não informada"
    paragrafos = [
        f"O imóvel denominado {nome}, localizado em {municipio}/{uf}, possui área de "
        f"{summary.area_ha:.4f} hectares e perímetro de {summary.perimetro_m:.2f} metros.",
        f"As coordenadas abaixo estão expressas em SIRGAS 2000 / UTM, código EPSG:{summary.epsg}.",
        "A descrição inicia no vértice V1 e segue os vértices na ordem apresentada, retornando ao ponto inicial.",
    ]
    return "\n\n".join(paragrafos)


def _linhas_tabela(summary: ParcelSummary) -> list[str]:
    return [
        f"{vertice.id:<8} {vertice.e:>12.2f} {vertice.n:>14.2f} "
        f"{vertice.azimute:>14} {vertice.distancia_m:>14.2f}"
        for vertice in summary.vertices
    ]


def montar_memorial(summary: ParcelSummary) -> tuple[QgsProject, QgsPrintLayout]:
    """Projeto e layout do memorial; o projeto precisa viver enquanto o layout for usado."""
    project = QgsProject()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.pageCollection().page(0).setPageSize("A4", QgsLayoutItemPage.Orientation.Portrait)
    paginador = _Paginador(layout)

    _label(layout, "GeoLume — Memorial Descritivo Preliminar", 16, X, TOPO)
    propriedades = _propriedades(summary)
    identificacao = "\n".join(
        linha
        for linha in [
            f"Imóvel: {propriedades.get('nome_imovel') or 'Não informado'}",
            f"Proprietário: {propriedades.get('proprietario') or 'Não informado'}",
            f"Município/UF: {propriedades.get('municipio') or 'Não informado'}/{propriedades.get('uf') or 'Não informado'}",
            f"Matrícula: {propriedades.get('matricula') or 'Não informada'}",
            f"Área: {summary.area_ha:.4f} ha    Perímetro: {summary.perimetro_m:.2f} m",
            f"Sistema de referência: SIRGAS 2000 / UTM — EPSG:{summary.epsg}",
        ]
    )
    # Quebra por largura medida (fonte proporcional): nada passa da margem direita.
    fim = paginador.bloco(32, quebrar_por_largura(layout, identificacao, 10, LARGURA), 10)
    # Na primeira página as seções mantêm as posições de antes (88 e 145 mm).
    y = fim + 6 if paginador.pagina else max(88, fim + 6)
    fim = paginador.bloco(
        y,
        _linhas_tabela(summary),
        8,
        titulo="Quadro de vértices",
        titulo_continuacao="Quadro de vértices (continuação)",
        cabecalho=_CABECALHO_TABELA,
    )
    y = fim + 8 if paginador.pagina else max(145, fim + 8)
    descricao = quebrar_por_largura(layout, memorial_text(summary), 10, LARGURA)
    paginador.bloco(y, descricao, 10, titulo="Descrição perimetral")
    return project, layout


def export_memorial_pdf(summary: ParcelSummary, output_path: Path) -> Path:
    project, layout = montar_memorial(summary)  # noqa: F841 — o projeto vive até a exportação
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.dpi = 300
    settings.textRenderFormat = Qgis.TextRenderFormat.AlwaysText
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resultado = QgsLayoutExporter(layout).exportToPdf(str(output_path), settings)
    if resultado != QgsLayoutExporter.ExportResult.Success:
        raise RuntimeError(f"Falha ao exportar memorial: {resultado}")
    return output_path

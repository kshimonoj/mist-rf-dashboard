"""PPTX のネイティブグラフの描画属性テスト。合成データのみを使う。

27 番で xlsx（openpyxl）が踏んだ「``delete=False`` を書かないと軸が消える」は
python-pptx では起きない（既定で軸が出る）。**それでも軸が出ていることを
明示的に固定する** ―― 見た目の設定はいちど崩れると気づきにくいため。

ここで固定するのは LibreOffice で PDF 化して目視確認した設定:

- 軸を消さない（``visible`` が True）
- カテゴリ軸は ``maxMin``（先頭が上）。既定のままだと時系列も順位も上下が逆になる
- 目盛ラベルのサイズは項目数で決める。既定（18pt）のままだと 20 件を超えたあたりで
  LibreOffice がラベルを間引き、どの棒が何か読めなくなる
- 積み上げは ``stacked`` + ``overlap=100``（100 でないと横に並ぶ）
- 系列／データ点の色は **保存済み結果の色定義**（``class_colors`` / ``model_colors``）
"""
from __future__ import annotations

import _repsynth as S
import pytest
from pptx.oxml.ns import qn
from pptx.util import Pt

from report import analysis, builder, charts

CHART_NS = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"


@pytest.fixture
def dirs(tmp_path) -> analysis.ResultsDirs:
    d = analysis.ResultsDirs.under(tmp_path)
    S.write_hangap(d.hangap)
    S.write_floorpeak(d.floorpeak)
    S.write_rrm(d.rrm)
    return d


def _charts(result: analysis.ReportResult) -> list:
    return [
        shape.chart
        for slide in result.presentation.slides
        for shape in slide.shapes
        if getattr(shape, "has_chart", False)
    ]


def _orientation(axis) -> str | None:
    scaling = axis._element.find(qn("c:scaling"))
    orientation = scaling.find(qn("c:orientation"))
    return None if orientation is None else orientation.get("val")


def _fill_of(element) -> str | None:
    """``spPr/solidFill/srgbClr`` の色。無ければ None。"""
    color = element.find(
        f"{CHART_NS}spPr/"
        "{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill/"
        "{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr"
    )
    return None if color is None else color.get("val")


def _series_fill(series) -> str | None:
    return _fill_of(series._element)


def _point_fills(series) -> list[str | None]:
    """棒ごとの色（``c:dPt`` を ``c:idx`` の順に並べる）。

    ``point._element`` は ``c:ser`` を返すので、点の色は ``c:dPt`` から直接読む。
    """
    points = series._element.findall(f"{CHART_NS}dPt")
    points.sort(key=lambda dpt: int(dpt.find(f"{CHART_NS}idx").get("val")))
    return [_fill_of(dpt) for dpt in points]


def _build(dirs, **kwargs):
    return analysis.run_report(analysis.ReportParams(**kwargs), dirs)


# ---------------------------------------------------------------------------
# 共通（すべてのグラフ）
# ---------------------------------------------------------------------------


def test_every_chart_keeps_its_axes(dirs):
    """**軸を消さない。** レポートに載るすべてのグラフで確認する。"""
    result = _build(
        dirs, hangap_result=S.HANGAP_NAME, floorpeak_result=S.FLOORPEAK_NAME,
        rrm_result=S.RRM_NAME,
    )
    built = _charts(result)
    assert len(built) == 4  # フロア 2 + 時間帯別 + インパクト
    for chart in built:
        assert chart.category_axis.visible is True
        assert chart.value_axis.visible is True
        assert chart.has_title is True


def test_every_chart_puts_the_first_category_on_top(dirs):
    result = _build(
        dirs, floorpeak_result=S.FLOORPEAK_NAME, rrm_result=S.RRM_NAME,
    )
    for chart in _charts(result):
        assert _orientation(chart.category_axis) == "maxMin"


def test_every_chart_shrinks_tick_labels(dirs):
    """既定（18pt）のままにしない。項目が増えるほど小さくする。"""
    result = _build(dirs, floorpeak_result=S.FLOORPEAK_NAME, rrm_result=S.RRM_NAME)
    for chart in _charts(result):
        size = chart.category_axis.tick_labels.font.size
        assert size is not None and size <= Pt(11)


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, Pt(11)), (12, Pt(11)), (13, Pt(9)), (24, Pt(9)), (25, Pt(7)), (32, Pt(7)),
     (33, Pt(5)), (builder.MAX_CHART_BUCKETS, Pt(5))],
)
def test_label_size_tiers(count, expected):
    assert charts._label_size(count) == expected


# ---------------------------------------------------------------------------
# RRM: 時間帯別の積み上げ棒
# ---------------------------------------------------------------------------


def test_rrm_hourly_chart_is_stacked_with_meta_colors(dirs):
    result = _build(dirs, rrm_result=S.RRM_NAME)
    meta = result.sources[0].meta
    hourly_chart = _charts(result)[0]

    plot = hourly_chart.plots[0]
    assert plot._element.find(f"{CHART_NS}grouping").get("val") == "stacked"
    assert plot.overlap == 100
    assert plot.gap_width == charts.GAP_WIDTH
    assert hourly_chart.has_legend is True

    colors = meta["class_colors"]
    for series, name in zip(plot.series, meta["classifications"]):
        assert _series_fill(series) == colors[name]


# ---------------------------------------------------------------------------
# Floor Peak: フロア別トップ 20
# ---------------------------------------------------------------------------


def test_floorpeak_chart_colors_each_bar_by_model(dirs):
    """単一系列なので標準の凡例は使えない。**棒ごと**に色を付ける。"""
    result = _build(dirs, floorpeak_result=S.FLOORPEAK_NAME)
    source = result.sources[0]
    colors = source.meta["model_colors"]
    default = source.meta["default_model_color"]

    for chart, floor in zip(_charts(result), source.meta["floors"]):
        assert chart.has_legend is False
        plot = chart.plots[0]
        assert plot.has_data_labels is True
        rows = source.rows[source.rows["map_name"] == floor["map_name"]]
        rows = rows.sort_values("rank_in_floor").head(builder.FLOORPEAK_TOP_N)
        expected = [colors.get(str(m), default) for m in rows["model"]]
        assert _point_fills(plot.series[0]) == expected


def test_unknown_model_falls_back_to_default_color(tmp_path):
    dirs = analysis.ResultsDirs.under(tmp_path)
    rows = S.floorpeak_rows(1)
    rows[0]["model"] = "TEST-UNKNOWN-MODEL"
    S.write_floorpeak(dirs.floorpeak, rows=rows)
    result = _build(dirs, floorpeak_result=S.FLOORPEAK_NAME)
    plot = _charts(result)[0].plots[0]
    default = result.sources[0].meta["default_model_color"]
    assert _point_fills(plot.series[0]) == [default]


# ---------------------------------------------------------------------------
# RRM: インパクト
# ---------------------------------------------------------------------------


def test_rrm_impact_chart_uses_class_colors(dirs):
    result = _build(dirs, rrm_result=S.RRM_NAME)
    meta = result.sources[0].meta
    impact_chart = _charts(result)[1]

    assert impact_chart.has_legend is False
    plot = impact_chart.plots[0]
    assert plot.has_data_labels is True
    expected = [meta["class_colors"][c["classification"]] for c in meta["by_classification"]]
    assert _point_fills(plot.series[0]) == expected


def test_broken_color_falls_back_to_grey(tmp_path):
    """色の定義が壊れていてもグラフを落とさない（灰色にする）。"""
    dirs = analysis.ResultsDirs.under(tmp_path)
    S.write_rrm(dirs.rrm, meta={"class_colors": {"RADAR": "zzz", "POST_RADAR": "", "RRM": None}})
    result = _build(dirs, rrm_result=S.RRM_NAME)
    plot = _charts(result)[0].plots[0]
    assert [_series_fill(s) for s in plot.series] == [charts.FALLBACK_COLOR] * 3

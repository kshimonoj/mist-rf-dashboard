"""横断レポート（PPTX）のパッケージ。

Hang AP / Floor Peak / RRM の **保存済み分析結果** を読み、1 つの PowerPoint に
まとめる。**分析は一切しない。** 各モジュールの ``archive`` が保存した組
（csv + json）を読むだけで、レポートのために新しい集計ロジックを持たない。

要点:

- 章立ては **Hang AP → Floor Peak → RRM の固定順**。選ばれたモジュールだけが
  この順で並ぶ（1 つだけ・2 つだけの選択も可）。3 つとも未選択はエラー。
- グラフはブラウザ（recharts）の見た目のキャプチャではなく、
  **python-pptx のネイティブグラフ**としてサーバ側で描き直す。
- 生成物は保存しない（``hangap_results`` のような一覧を持たない）。都度
  「選択 → 生成 → ダウンロード」で完結する。

``hangap`` / ``floorpeak`` / ``rrm`` パッケージは **読むだけ**で、変更しない。
ネットワークアクセス・LLM 呼び出しは行わない。
"""
from .analysis import (
    PHASE_BUILDING,
    PHASE_LOADING,
    PHASE_WRITING,
    SECTION_LABELS,
    SECTION_ORDER,
    ParamError,
    ReportError,
    ReportParams,
    ReportResult,
    ResultsDirs,
    SlideInfo,
    Source,
    build_report,
    load_sources,
    output_name,
    run_report,
    write_pptx,
)

__all__ = [
    "ReportParams",
    "ReportResult",
    "ResultsDirs",
    "Source",
    "SlideInfo",
    "ReportError",
    "ParamError",
    "SECTION_ORDER",
    "SECTION_LABELS",
    "PHASE_LOADING",
    "PHASE_BUILDING",
    "PHASE_WRITING",
    "load_sources",
    "build_report",
    "write_pptx",
    "run_report",
    "output_name",
]

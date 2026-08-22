"""RRM / RADAR チャネル変更分析のパッケージ。

``ap_events`` を主データ源として、``AP_RRM_ACTION`` によるチャネル変更を
**RADAR / POST_RADAR / RRM** の 3 分類で数え、変更前後の ``ap_metrics``
（接続端末数と 2.4 / 5 / 6GHz の利用率）を突き合わせる。

要点:

- ``pre_channel == channel`` は「評価のみ（no-op）」として **別に数える**。
  除外して見えなくしない（RRM が動作していること自体が情報である）。
- ``AP_RADAR_DETECTED`` は ``AP_RRM_ACTION`` と **独立に** 数える。対応する
  ACTION が記録されていない検知があるため、ACTION だけではレーダーを取りこぼす。
- 前後サンプルは平均を取らず、直前 1 件・直後 1 件をそのまま出す。照合できない行は
  理由（``match_status``）を残し、前後区間に別の変更があった行には汚染の印を付ける。
  **どちらの行も除外しない。**

``hangap`` / ``floorpeak`` パッケージとはコードを共有しない。読み込みだけ
:mod:`hangap.loader` を再利用する（同じログを 2 通りに読まないため）。
"""
from .analysis import (
    BUCKET_SECONDS,
    RESULT_COLUMNS,
    AnalysisParams,
    AnalysisResult,
    run_analysis,
)
from .events import (
    CLASS_COLORS,
    CLASS_POST_RADAR,
    CLASS_RADAR,
    CLASS_RRM,
    CLASSIFICATIONS,
    RadarSummary,
    action_frame,
    classify,
    radar_summary,
)
from .loader import RrmLoad, load_logs
from .metrics import (
    MATCH_NO_AFTER,
    MATCH_NO_AP,
    MATCH_NO_BEFORE,
    MATCH_OK,
    MATCH_TOO_FAR,
    ChangeEventIndex,
    MetricIndex,
)

__all__ = [
    "run_analysis",
    "AnalysisParams",
    "AnalysisResult",
    "RESULT_COLUMNS",
    "BUCKET_SECONDS",
    "load_logs",
    "RrmLoad",
    "action_frame",
    "classify",
    "radar_summary",
    "RadarSummary",
    "CLASSIFICATIONS",
    "CLASS_RADAR",
    "CLASS_POST_RADAR",
    "CLASS_RRM",
    "CLASS_COLORS",
    "MetricIndex",
    "ChangeEventIndex",
    "MATCH_OK",
    "MATCH_NO_BEFORE",
    "MATCH_NO_AFTER",
    "MATCH_TOO_FAR",
    "MATCH_NO_AP",
]

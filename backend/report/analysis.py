"""横断レポート生成のエントリポイント（ジョブ API と CLI が共有する）。

ここで行うのは 3 つだけ:

1. 各モジュールの ``archive`` から **保存済み** の分析結果（json + csv）を読む
2. :mod:`report.builder` にスライドを組み立てさせる
3. PPTX として書き出す

**分析は走らせない。** 保存済みの結果が無ければエラーにする（黙って空の
レポートを作らない）。3 つとも未選択の場合も同じくエラー。

``hangap`` / ``floorpeak`` / ``rrm`` は **読むだけ**。各モジュールの csv 読み戻し
（``hangap.table.read_result_csv`` など）をそのまま使い、ここで別実装を持たない。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd
from pptx import Presentation

from floorpeak import analysis as fp_analysis, archive as fp_archive
from hangap import archive as ha_archive, table as ha_table
from rrm import analysis as rrm_analysis, archive as rrm_archive

from . import builder

#: 章の並び。**この順を変えないこと**（選んだ順ではなく常にこの順で並べる）
SECTION_ORDER: tuple[str, ...] = ("hangap", "floorpeak", "rrm")

SECTION_LABELS: dict[str, str] = {
    "hangap": "Hang AP",
    "floorpeak": "Floor Peak",
    "rrm": "RRM",
}

#: リクエスト／CLI で章を指す名前
SECTION_FIELDS: dict[str, str] = {
    "hangap": "hangap_result",
    "floorpeak": "floorpeak_result",
    "rrm": "rrm_result",
}

PHASE_LOADING = "loading"
PHASE_BUILDING = "building"
PHASE_WRITING = "writing"

OUTPUT_PREFIX = "report_"
STAMP_FORMAT = "%Y%m%d_%H%M%S"


class ReportError(RuntimeError):
    """レポートを作れない（入力エラー）。"""


class ParamError(ReportError):
    """パラメータが不正（API では 400 にする）。"""


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportParams:
    """レポートに含める保存済み結果の名前（各モジュール 0 件または 1 件）。"""

    hangap_result: str | None = None
    floorpeak_result: str | None = None
    rrm_result: str | None = None

    def name_for(self, section: str) -> str | None:
        value = getattr(self, SECTION_FIELDS[section])
        text = str(value).strip() if value is not None else ""
        return text or None

    def selected(self) -> tuple[str, ...]:
        """選ばれた章を **固定順** で返す（選んだ順序には依存しない）。"""
        return tuple(s for s in SECTION_ORDER if self.name_for(s) is not None)


@dataclass(frozen=True)
class ResultsDirs:
    """各モジュールの保存先。テストで差し替えられるよう値で持ち回す。"""

    hangap: Path
    floorpeak: Path
    rrm: Path

    @classmethod
    def under(cls, data_dir: str | Path) -> "ResultsDirs":
        root = Path(data_dir)
        return cls(
            hangap=root / ha_archive.RESULTS_DIR_NAME,
            floorpeak=root / fp_archive.RESULTS_DIR_NAME,
            rrm=root / rrm_archive.RESULTS_DIR_NAME,
        )

    def for_section(self, section: str) -> Path:
        return Path(getattr(self, section))


@dataclass(frozen=True)
class Source:
    """レポートに載せる 1 章ぶんの保存済み結果。"""

    section: str
    name: str
    meta: dict[str, Any]
    rows: pd.DataFrame

    @property
    def label(self) -> str:
        return SECTION_LABELS[self.section]


@dataclass(frozen=True)
class SlideInfo:
    """作ったスライド 1 枚の素性（テスト・API のレスポンスで使う）。"""

    section: str
    kind: str
    title: str


@dataclass
class ReportResult:
    presentation: Presentation
    slides: list[SlideInfo]
    sources: list[Source]
    generated_at: datetime

    @property
    def sections(self) -> list[str]:
        """含まれる章（固定順）。"""
        return [s.section for s in self.sources]

    @property
    def slide_count(self) -> int:
        return len(self.slides)


# ---------------------------------------------------------------------------
# 保存済み結果の読み込み
# ---------------------------------------------------------------------------

#: 章 → (archive モジュール, csv 読み戻し関数)
_READERS: dict[str, tuple[Any, Callable[[Path], pd.DataFrame]]] = {
    "hangap": (ha_archive, ha_table.read_result_csv),
    "floorpeak": (fp_archive, fp_analysis.read_result_csv),
    "rrm": (rrm_archive, rrm_analysis.read_result_csv),
}


def _load_one(section: str, name: str, results_dir: Path) -> Source:
    archive, read_csv = _READERS[section]
    label = SECTION_LABELS[section]

    if not archive.is_valid_name(name):
        raise ParamError(
            f"{label}: 保存済み結果の名前が不正です（{archive.NAME_PREFIX}YYYYMMDD_HHMMSS）: {name!r}"
        )

    result_set = next(
        (s for s in archive.list_sets(results_dir) if s.name == name), None
    )
    if result_set is None:
        raise ReportError(f"{label}: 保存済みの分析結果が見つかりません: {name}")

    csv_path = archive.member_path(results_dir, name, ".csv")
    if not csv_path.is_file():
        raise ReportError(f"{label}: 保存済みの csv がありません: {name}.csv")
    try:
        rows = read_csv(csv_path)
    except (OSError, ValueError) as exc:
        raise ReportError(f"{label}: 保存済みの csv を読み込めません（{name}.csv）: {exc}") from None

    # meta は一覧（/results）と同じ形にする。json が壊れていても形は崩れない
    return Source(section=section, name=name, meta=archive.describe(result_set), rows=rows)


def load_sources(params: ReportParams, dirs: ResultsDirs) -> list[Source]:
    """選ばれた章の保存済み結果を **固定順** で読む。

    3 つとも未選択ならエラー（空のレポートは作らない）。
    """
    selected = params.selected()
    if not selected:
        raise ParamError(
            "レポートに含める分析結果が 1 つも選ばれていません。"
            "Hang AP / Floor Peak / RRM のいずれかを選んでください"
        )
    return [
        _load_one(section, params.name_for(section), dirs.for_section(section))
        for section in selected
    ]


# ---------------------------------------------------------------------------
# 組み立て・書き出し
# ---------------------------------------------------------------------------


def build_report(
    sources: Sequence[Source], *, generated_at: datetime | None = None
) -> ReportResult:
    """表紙 + 選ばれた章のスライドを組み立てる。

    ``sources`` の並びは無視し、**必ず** :data:`SECTION_ORDER` の順に並べる。
    """
    if not sources:
        raise ParamError("レポートに含める分析結果がありません")

    stamp = generated_at or datetime.now(timezone.utc)
    ordered = sorted(sources, key=lambda s: SECTION_ORDER.index(s.section))

    prs = builder.new_presentation()
    title = builder.build_cover(prs, ordered, stamp)
    slides = [SlideInfo(section="cover", kind="cover", title=title)]

    for source in ordered:
        for kind, slide_title in builder.SECTION_BUILDERS[source.section](prs, source):
            slides.append(SlideInfo(section=source.section, kind=kind, title=slide_title))

    return ReportResult(
        presentation=prs, slides=slides, sources=list(ordered), generated_at=stamp
    )


def output_name(generated_at: datetime) -> str:
    """出力ファイル名（保存はしないので、名前は生成時刻だけで決める）。"""
    return f"{OUTPUT_PREFIX}{generated_at.strftime(STAMP_FORMAT)}.pptx"


def write_pptx(path: str | Path, result: ReportResult) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.presentation.save(str(out))
    return out


def run_report(
    params: ReportParams,
    dirs: ResultsDirs,
    *,
    generated_at: datetime | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> ReportResult:
    """読み込み → 組み立て。書き出しは呼び出し側（:func:`write_pptx`）で行う。"""
    notify = on_phase or (lambda _phase: None)
    notify(PHASE_LOADING)
    sources = load_sources(params, dirs)
    notify(PHASE_BUILDING)
    return build_report(sources, generated_at=generated_at)

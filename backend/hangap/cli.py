"""hangap CLI — ローダ・検出エンジン・トポロジ診断を呼び出すだけの薄い配線。

``hangap.loader.load()`` / ``hangap.detector.detect()`` / ``hangap.topology.analyze()`` /
``hangap.neighbors.build_context()`` のロジックはここでは再実装しない。
analyze の本体（分析パイプラインと出力の書き出し）は ``hangap.analysis`` にあり、
API（``routers/hangap.py``）と共用する。
ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

import argparse
import glob as globlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import analysis, detector, loader, neighbors, topology

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_OUTPUT_ERROR = 2

class CliError(RuntimeError):
    """入力エラー（終了コード 1）。"""


class OutputError(RuntimeError):
    """出力エラー（終了コード 2）。"""


class _ArgumentParser(argparse.ArgumentParser):
    """argparse の既定終了コード(2)を使わせないための薄いラッパ。"""

    def error(self, message: str) -> None:  # noqa: D102 - argparse のオーバーライド
        self.print_usage(sys.stderr)
        raise CliError(f"{self.prog}: {message}")


# ---------------------------------------------------------------------------
# 引数パース
# ---------------------------------------------------------------------------


def build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="hangap",
        description="ハングAP候補（ゼロクライアント区間）を Mist ログから検出する",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="ログを読み込み、ゼロクライアント区間を検出する")
    p.add_argument("inputs", nargs="*", metavar="INPUT",
                    help="ファイル・ディレクトリ・glob パターン（複数可）")
    p.add_argument("--metrics", nargs="+", metavar="PATH", default=[],
                    help="メトリクスのみを明示指定（任意）")
    p.add_argument("--events", nargs="+", metavar="PATH", default=[],
                    help="イベントのみを明示指定（任意）")
    p.add_argument("--from", dest="window_from", metavar="TIME", default=None,
                    help="分析対象期間の開始（YYYY-MM-DD HH:MM または ISO8601、TZ 無し）。"
                         "指定するとこの期間のサンプルだけで分析する")
    p.add_argument("--to", dest="window_to", metavar="TIME", default=None,
                    help="分析対象期間の終了（YYYY-MM-DD HH:MM または ISO8601、TZ 無し）。"
                         "この時点でゼロが続く区間は「継続中」になる")
    p.add_argument("--min-zero-samples", type=int,
                    default=detector.DEFAULT_MIN_ZERO_SAMPLES)
    p.add_argument("--min-zero-duration", metavar="DURATION", default=None,
                    help="例: 30m / 25min / 1h。指定時は --min-zero-samples より優先")
    p.add_argument("--event-window", metavar="DURATION", default="30m")
    p.add_argument("--exodus-threshold", type=float,
                    default=detector.DEFAULT_EXODUS_THRESHOLD)
    p.add_argument("--gap-factor", type=float, default=loader.DEFAULT_GAP_FACTOR)
    # 周辺AP判定（距離ベース）。既定値はいずれも暫定であり、実データを見ながら調整する前提。
    p.add_argument("--neighbor-count", type=int, default=neighbors.DEFAULT_NEIGHBOR_COUNT,
                    help=f"近傍として採用する最大台数（既定 {neighbors.DEFAULT_NEIGHBOR_COUNT}・暫定値）")
    p.add_argument("--max-distance-m", type=float, default=neighbors.DEFAULT_MAX_DISTANCE_M,
                    help=f"近傍として認める最大距離 m（既定 {neighbors.DEFAULT_MAX_DISTANCE_M:g}・暫定値）")
    p.add_argument("--neighbor-client-threshold", type=float,
                    default=neighbors.DEFAULT_NEIGHBOR_CLIENT_THRESHOLD,
                    help="周辺AP端末数合計がこれ以上なら「周辺に端末あり」"
                         f"（既定 {neighbors.DEFAULT_NEIGHBOR_CLIENT_THRESHOLD:g}・暫定値）")
    p.add_argument("--truncated-warn-ratio", type=float,
                    default=detector.DEFAULT_TRUNCATED_WARN_RATIO,
                    help="打ち切り(欠測)の比率がこれを超えたら警告する"
                         f"（既定 {detector.DEFAULT_TRUNCATED_WARN_RATIO:g}）")
    p.add_argument("--explain", metavar="AP_NAME", action="append", default=[],
                    help="指定した AP の各区間について判定根拠を表示する（複数指定可）")
    p.add_argument("--out", required=True, metavar="DIR", help="出力先ディレクトリ（必須）")
    p.add_argument("--format", choices=("xlsx", "csv", "both"), default="both")

    t = sub.add_parser(
        "topology-report",
        help="RF 隣接（rf_neighbors）と地図上の距離隣接のズレを実測する",
    )
    t.add_argument("inputs", nargs="*", metavar="INPUT",
                   help="ファイル・ディレクトリ・glob パターン（複数可）")
    t.add_argument("--out", required=True, metavar="DIR", help="出力先ディレクトリ（必須）")
    t.add_argument("--band", default=topology.DEFAULT_BAND,
                   help=f"対象バンド（24 / 5 / 6。既定 {topology.DEFAULT_BAND}）")
    t.add_argument("--top-n", dest="top_n", metavar="N[,N...]",
                   default=",".join(str(n) for n in topology.DEFAULT_TOP_N),
                   help="評価する上位N台（カンマ区切り。既定 "
                        f"{','.join(str(n) for n in topology.DEFAULT_TOP_N)}）")
    return parser


# ---------------------------------------------------------------------------
# 入力の解決
# ---------------------------------------------------------------------------


def _resolve_one(raw: str) -> list[Path]:
    # 走査対象の判定は loader.is_data_file に委ねる（hangap 自身の出力を置く
    # hangap_results 配下を、ここと loader で別々に除外しないため）。
    p = Path(raw)
    if p.is_dir():
        found = [f for f in sorted(p.rglob("*")) if f.is_file() and loader.is_data_file(f)]
        if not found:
            raise CliError(f"ディレクトリに CSV/XLSX が見つかりません: {raw}")
        return found
    if p.is_file():
        return [p]

    matches = sorted(globlib.glob(raw, recursive=True))
    if not matches:
        raise CliError(f"入力パスが見つかりません: {raw}")
    out: list[Path] = []
    for hit in matches:
        hp = Path(hit)
        if hp.is_dir():
            out.extend(f for f in sorted(hp.rglob("*")) if f.is_file() and loader.is_data_file(f))
        elif hp.is_file():
            out.append(hp)
    if not out:
        raise CliError(f"入力パスが見つかりません: {raw}")
    return out


def resolve_inputs(raw_paths: Sequence[str]) -> list[Path]:
    """ファイル・ディレクトリ・glob パターンを、重複を除いた具体的なファイル一覧へ展開する。

    ``loader.load()`` 自身も同じ探索を行うが、ここでは「見つからない入力を明示的に
    エラーにする」ための検証を目的とする（loader 側は判定できないだけなら黙って続行する）。
    """
    seen: set[Path] = set()
    files: list[Path] = []
    for raw in raw_paths:
        for f in _resolve_one(raw):
            rp = f.resolve()
            if rp not in seen:
                seen.add(rp)
                files.append(f)
    return files


def _check_output_not_input(out_dir: str, raw_paths: Sequence[str], files: Sequence[Path]) -> None:
    out_real = Path(out_dir).resolve()
    for raw in raw_paths:
        p = Path(raw)
        if p.is_dir() and p.resolve() == out_real:
            raise OutputError(f"--out が入力ディレクトリと同一です: {out_dir}")
    for f in files:
        if f.resolve().parent == out_real:
            raise OutputError(f"--out が入力ファイルの所在ディレクトリと同一です: {out_dir}")


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """サブコマンドごとの処理へ振り分ける。"""
    if args.command == "topology-report":
        return run_topology_report(args)
    return run_analyze(args)


def _parse_top_n(text: str) -> tuple[int, ...]:
    values: list[int] = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError as exc:
            raise CliError(f"--top-n を解釈できません: {text!r}") from exc
        if n <= 0:
            raise CliError(f"--top-n は 1 以上で指定してください: {text!r}")
        values.append(n)
    if not values:
        raise CliError(f"--top-n を解釈できません: {text!r}")
    return tuple(sorted(set(values)))


def run_topology_report(args: argparse.Namespace) -> int:
    raw_paths = list(args.inputs)
    if not raw_paths:
        raise CliError("入力パスを指定してください")

    files = resolve_inputs(raw_paths)
    _check_output_not_input(args.out, raw_paths, files)
    top_n = _parse_top_n(args.top_n)

    load_result = loader.load(files)
    report = load_result.report

    if report.metrics_rows == 0 and report.rf_neighbors_rows == 0 and report.unclassified:
        sample = ", ".join(report.unclassified[:5])
        more = " ..." if len(report.unclassified) > 5 else ""
        raise CliError(
            "入力ファイルの種別を判定できませんでした"
            f"（ap_metrics / rf_neighbors のいずれにも一致しません）: {sample}{more}"
        )

    result = topology.analyze(
        load_result.metrics,
        load_result.rf_neighbors,
        band=args.band,
        top_n=top_n,
    )

    text = result.render()
    print(report.render())
    print()
    print(text)
    print()

    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"出力先ディレクトリを作成できません: {args.out} ({exc})") from exc

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = out_dir / f"topology_report_{stamp}.txt"
    csv_path = out_dir / f"topology_report_{stamp}.csv"
    try:
        txt_path.write_text(text + "\n", encoding="utf-8")
        result.detail.to_csv(csv_path, index=False, encoding="utf-8-sig")
    except OSError as exc:
        raise OutputError(f"出力ファイルの書き込みに失敗しました: {exc}") from exc

    print("[ 出力ファイル ]")
    for p in (txt_path, csv_path):
        print(f"  {p}")
    return EXIT_OK


def run_analyze(args: argparse.Namespace) -> int:
    raw_paths = list(args.inputs) + list(args.metrics) + list(args.events)
    if not raw_paths:
        raise CliError("入力パスを指定してください（位置引数 / --metrics / --events のいずれか）")

    files = resolve_inputs(raw_paths)
    _check_output_not_input(args.out, raw_paths, files)

    params = analysis.AnalysisParams(
        window_start=(
            analysis.parse_time(args.window_from, "--from") if args.window_from else None
        ),
        window_end=analysis.parse_time(args.window_to, "--to") if args.window_to else None,
        min_zero_samples=args.min_zero_samples,
        min_zero_duration=(
            analysis.parse_duration(args.min_zero_duration, "--min-zero-duration")
            if args.min_zero_duration
            else None
        ),
        event_window=analysis.parse_duration(args.event_window, "--event-window"),
        exodus_threshold=args.exodus_threshold,
        gap_factor=args.gap_factor,
        neighbor_count=args.neighbor_count,
        max_distance_m=args.max_distance_m,
        neighbor_client_threshold=args.neighbor_client_threshold,
        truncated_warn_ratio=args.truncated_warn_ratio,
    )

    res = analysis.run_analysis(files, params)
    result_df = res.result
    meta = res.meta()

    # 1. ローダのレポート
    print(res.report.render())
    print()
    # 2. 警告（検出時。データ範囲不足など）
    print(f"[ 警告（検出時） ] {len(res.detector_warnings)} 件")
    if not res.detector_warnings:
        print("  （なし）")
    for w in res.detector_warnings:
        print(f"  ! {w}")
    print()
    # 3. 分析条件
    print(meta.condition_text)
    print()
    # 4. 結果サマリー
    print("[ 結果サマリー ]")
    print(meta.result_summary_text)
    print()
    # 5. データ品質の警告（件数だけを見ていると気づけないため独立した節にする）
    if res.quality_warnings:
        print("[ データ品質の警告 ]")
        for w in res.quality_warnings:
            print(f"  ⚠ 警告: {w}")
        print()
    # 6. 判定根拠（--explain）
    if args.explain:
        print(neighbors.render_explain(result_df, args.explain, res.neighbor_context))
        print()

    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"出力先ディレクトリを作成できません: {args.out} ({exc})") from exc

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written: list[Path] = []
    try:
        if args.format in ("xlsx", "both"):
            written.append(
                analysis.write_xlsx(out_dir / f"hangap_result_{stamp}.xlsx", result_df, meta)
            )
        if args.format in ("csv", "both"):
            written.append(analysis.write_csv(out_dir / f"hangap_result_{stamp}.csv", result_df))
            # xlsx には条件・警告を埋め込めるが、CSV 単体は表形式のみのため、
            # --format both でも CSV を受け取った人が読めるよう summary は常に添える。
            written.append(
                analysis.write_summary(out_dir / f"hangap_result_{stamp}_summary.txt", meta)
            )
    except OSError as exc:
        raise OutputError(f"出力ファイルの書き込みに失敗しました: {exc}") from exc

    # 7. 出力ファイルのパス
    print("[ 出力ファイル ]")
    for p in written:
        print(f"  {p}")

    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return run(args)
    # NoMetricsError（ap_metrics 0 行）もここに入る。「検出0件」は正常終了(0)だが、
    # 「そもそも分析対象が無かった」は入力エラーとして 1 で終わること。
    except (CliError, analysis.AnalysisError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OutputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_OUTPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

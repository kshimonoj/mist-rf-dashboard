"""rrm CLI — ローダ・分類・突合・集計を呼び出すだけの薄い配線。

分析の本体（パイプラインと出力の書き出し）は :mod:`rrm.analysis` にあり、
API（``routers/rrm.py``）と共用する。ここでロジックを再実装しない。

``--site`` は **複数指定できる**（省略すると全サイト）。floorpeak と違い
「サイト全体のピーク」のような単一サイト前提の定義が無く、サイト別比較を出すため。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import analysis, events as ev, loader

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


def build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="rrm",
        description="RRM / RADAR によるチャネル変更と、その前後のメトリクスを出す",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="チャネル変更の明細と集計を出す")
    p.add_argument("--logs", required=True, metavar="DIR",
                   help="ログのディレクトリ（再帰探索）")
    p.add_argument("--site", dest="sites", action="append", default=None, metavar="SITE",
                   help="対象サイト（site_id または site_name）。複数指定可。省略すると全サイト")
    p.add_argument("--from", dest="window_from", metavar="TIME", default=None,
                   help="期間の開始（YYYY-MM-DD HH:MM または ISO8601、TZ 無し）")
    p.add_argument("--to", dest="window_to", metavar="TIME", default=None,
                   help="期間の終了（半開区間。この時刻ちょうどのイベントは含まない）")
    p.add_argument("--out", required=True, metavar="DIR", help="出力先ディレクトリ（必須）")
    p.add_argument("--format", choices=("xlsx", "csv", "both"), default="both")
    return parser


def run_analyze(args: argparse.Namespace) -> int:
    logs_dir = Path(args.logs)
    if not logs_dir.is_dir():
        raise CliError(f"--logs がディレクトリではありません: {args.logs}")

    out_dir = Path(args.out)
    if out_dir.resolve() == logs_dir.resolve():
        raise OutputError(f"--out が --logs と同一です: {args.out}")

    files = loader.collect_files(logs_dir)
    if not files:
        raise CliError(f"ディレクトリに CSV/XLSX が見つかりません: {args.logs}")

    params = analysis.AnalysisParams(
        sites=tuple(args.sites or ()),
        window_start=analysis.parse_time(args.window_from, "--from") if args.window_from else None,
        window_end=analysis.parse_time(args.window_to, "--to") if args.window_to else None,
    )
    if (
        params.window_start is not None
        and params.window_end is not None
        and params.window_start >= params.window_end
    ):
        raise CliError(f"--to は --from より後の時刻を指定してください: {args.window_to!r}")

    res = analysis.run_analysis(files, params)
    meta = res.meta

    print(analysis.summary_text(meta))
    print()
    print("[ 分類別 ]")
    for item in meta["by_classification"]:
        print(
            f"  {item['classification']:<12} 変更 {item['changes']:>5} 件 /"
            f" no-op {item['noop']:>5} 件 / インパクト合計 {item['impact_total']:>6} 台"
            f" / 平均 {item['impact_avg'] if item['impact_avg'] is not None else '-'}"
        )
    print()
    print("[ サイト別 ]")
    for item in meta["by_site"] or []:
        print(
            f"  {item['site_name']:<24} 変更 {item['changes']:>5} 件 /"
            f" no-op {item['noop']:>5} 件 / インパクト合計 {item['impact_total']:>6} 台"
        )
    if not meta["by_site"]:
        print("  （なし）")
    print()
    print(f"[ AP 別（上位 {meta['top_ap_count']}） ]")
    for item in meta["by_ap"] or []:
        print(f"  {item['ap_name']:<24} 変更 {item['changes']:>5} 件 / インパクト {item['impact_total']:>6} 台")
    if not meta["by_ap"]:
        print("  （なし）")
    print()

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"出力先ディレクトリを作成できません: {args.out} ({exc})") from exc

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written: list[Path] = []
    try:
        if args.format in ("xlsx", "both"):
            written.append(analysis.write_xlsx(out_dir / f"rrm_result_{stamp}.xlsx", res.rows, meta))
        if args.format in ("csv", "both"):
            written.append(analysis.write_csv(out_dir / f"rrm_result_{stamp}.csv", res.rows))
            # csv 単体は表形式のみなので、前提（条件・警告）を読める summary を必ず添える
            written.append(analysis.write_summary(out_dir / f"rrm_result_{stamp}_summary.txt", meta))
    except OSError as exc:
        raise OutputError(f"出力ファイルの書き込みに失敗しました: {exc}") from exc

    print("[ 出力ファイル ]")
    for p in written:
        print(f"  {p}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return run_analyze(args)
    # NoMetricsError / NoEventsError（対象 0 行）もここに入る。「チャネル変更が
    # 無かった」ではなく「そもそも分析対象が無かった」なので入力エラーの 1 で終わる。
    except (CliError, analysis.AnalysisError, loader.LoadError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OutputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_OUTPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

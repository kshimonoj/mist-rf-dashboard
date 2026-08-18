"""floorpeak CLI — ローダ・ピーク選定・フロア解決を呼び出すだけの薄い配線。

分析の本体（パイプラインと出力の書き出し）は :mod:`floorpeak.analysis` にあり、
API（``routers/floorpeak.py``）と共用する。ここでロジックを再実装しない。

``--at`` を指定したときは ``--from`` / ``--to`` を **無視する**。指定時点に最も近い
バケットを全期間から選ぶ（窓で絞ると、指定時点が窓の外にあったときに「最も近い
バケット」が窓の端に張り付き、選定根拠を説明できなくなる）。無視したことは
警告として結果に残る。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import analysis, loader

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
        prog="floorpeak",
        description="サイトの混雑ピーク時点を選び、フロア別の AP 接続端末数を出す",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="ピーク時点のフロア別 AP 接続端末数を出す")
    p.add_argument("--logs", required=True, metavar="DIR",
                   help="ログのディレクトリ（再帰探索）")
    p.add_argument("--site", required=True, metavar="SITE",
                   help="対象サイト（site_id または site_name）。**単一指定が必須**")
    p.add_argument("--from", dest="window_from", metavar="TIME", default=None,
                   help="期間の開始（YYYY-MM-DD HH:MM または ISO8601、TZ 無し）")
    p.add_argument("--to", dest="window_to", metavar="TIME", default=None,
                   help="期間の終了（半開区間。この時刻ちょうどのサンプルは含まない）")
    p.add_argument("--at", dest="at", metavar="TIME", default=None,
                   help="時点を手動指定する（指定すると --from / --to は無視する）")
    p.add_argument("--floor", dest="floor", metavar="NAME", default=None,
                   help="xlsx のグラフに出すフロア（既定は端末数が最も多いフロア）")
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
        site=args.site,
        window_start=analysis.parse_time(args.window_from, "--from") if args.window_from else None,
        window_end=analysis.parse_time(args.window_to, "--to") if args.window_to else None,
        at=analysis.parse_time(args.at, "--at") if args.at else None,
    )
    if (
        params.window_start is not None
        and params.window_end is not None
        and params.window_start >= params.window_end
    ):
        raise CliError(f"--to は --from より後の時刻を指定してください: {args.window_to!r}")

    res = analysis.run_analysis(files, params)
    meta = res.meta

    floor = args.floor or meta.get("default_floor")
    if args.floor and args.floor not in {f["map_name"] for f in meta.get("floors", [])}:
        available = ", ".join(f["map_name"] for f in meta.get("floors", []))
        raise CliError(f"--floor が結果に無いフロアです: {args.floor!r}（フロア: {available or 'なし'}）")

    print(analysis.summary_text(meta))
    print()
    print("[ フロア ]")
    for f in meta["floors"]:
        print(f"  {f['map_name']:<24} AP {f['ap_count']:>4} 台 / 端末 {f['num_clients']:>6}")
    print()
    print(f"[ グラフ対象フロア ] {floor or '（なし）'}")
    print()

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputError(f"出力先ディレクトリを作成できません: {args.out} ({exc})") from exc

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written: list[Path] = []
    try:
        if args.format in ("xlsx", "both"):
            written.append(
                analysis.write_xlsx(out_dir / f"floorpeak_result_{stamp}.xlsx", res.rows, meta, floor)
            )
        if args.format in ("csv", "both"):
            written.append(analysis.write_csv(out_dir / f"floorpeak_result_{stamp}.csv", res.rows))
            # csv 単体は表形式のみなので、前提（条件・警告）を読める summary を必ず添える
            written.append(
                analysis.write_summary(out_dir / f"floorpeak_result_{stamp}_summary.txt", meta)
            )
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
    # NoMetricsError（対象 0 行）もここに入る。「ピークが無かった」ではなく
    # 「そもそも分析対象が無かった」なので入力エラーとして 1 で終わること。
    except (CliError, analysis.AnalysisError, loader.LoadError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OutputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_OUTPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

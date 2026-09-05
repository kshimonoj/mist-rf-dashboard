"""report CLI — 保存済み結果を選んで PPTX を書き出すだけの薄い配線。

本体は :mod:`report.analysis` にあり、API（``routers/report.py``）と共用する。
ここでロジックを再実装しない。

    python -m report generate --data-dir ./data --rrm rrm_result_20260822_235453 --out ./out

3 つとも省略すると入力エラー（終了コード 1）。

ネットワークアクセス・LLM 呼び出しは行わない。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import analysis

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
        prog="report",
        description="Hang AP / Floor Peak / RRM の保存済み分析結果を 1 つの PPTX にまとめる",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="保存済み結果からレポート（PPTX）を作る")
    p.add_argument("--data-dir", required=True, metavar="DIR",
                   help="各モジュールの保存先を含むディレクトリ（hangap_results/ などの親）")
    p.add_argument("--hangap", dest="hangap_result", default=None, metavar="NAME",
                   help="hangap_result_YYYYMMDD_HHMMSS。省略すると Hang AP の章は作らない")
    p.add_argument("--floorpeak", dest="floorpeak_result", default=None, metavar="NAME",
                   help="floorpeak_result_YYYYMMDD_HHMMSS。省略すると Floor Peak の章は作らない")
    p.add_argument("--rrm", dest="rrm_result", default=None, metavar="NAME",
                   help="rrm_result_YYYYMMDD_HHMMSS。省略すると RRM の章は作らない")
    p.add_argument("--out", required=True, metavar="DIR", help="出力先ディレクトリ（必須）")
    return parser


def run_generate(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        raise CliError(f"--data-dir がディレクトリではありません: {args.data_dir}")

    params = analysis.ReportParams(
        hangap_result=args.hangap_result,
        floorpeak_result=args.floorpeak_result,
        rrm_result=args.rrm_result,
    )
    generated_at = datetime.now(timezone.utc)
    result = analysis.run_report(
        params, analysis.ResultsDirs.under(data_dir), generated_at=generated_at
    )

    print("[ 含まれる章 ]")
    for source in result.sources:
        print(f"  {source.label:<12} {source.name}")
    print()
    print("[ スライド ]")
    for index, slide in enumerate(result.slides, start=1):
        print(f"  {index:>2}. [{slide.section}] {slide.title}")
    print()

    try:
        written = analysis.write_pptx(
            Path(args.out) / analysis.output_name(generated_at), result
        )
    except OSError as exc:
        raise OutputError(f"出力ファイルの書き込みに失敗しました: {exc}") from exc

    print("[ 出力ファイル ]")
    print(f"  {written}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return run_generate(args)
    except (CliError, analysis.ReportError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OutputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_OUTPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

"""restore サブコマンド — 仮名化したファイルを元の値に戻す。

使用例:
    python -m pseudonymizer restore merged.csv --out ./restored
    python -m pseudonymizer restore out/*.csv --out ./restored --no-time

復元の入力は「加工・統合されたあとのファイル」を想定している。列定義は使わず、
テキストとして置換する（詳細は :mod:`pseudonymizer.restore`）。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

from .restore import (
    COUNT_LABELS,
    SUPPORTED_EXTENSIONS,
    FileReport,
    RestoreError,
    RestoreReport,
    load_engine,
)
from .salt import SaltError
from .transforms import PseudonymizeError


class RestoreCliError(RuntimeError):
    """CLI レベルの致命的エラー。"""


def resolve_inputs(patterns: list[str]) -> list[Path]:
    """ファイル・ディレクトリ・glob パターンを対応形式のファイル一覧に展開する。"""
    resolved: list[str] = []
    for pattern in patterns:
        if os.path.isdir(pattern):
            matched = sorted(
                p
                for p in glob.glob(os.path.join(pattern, "*"))
                if os.path.isfile(p) and Path(p).suffix.lower() in SUPPORTED_EXTENSIONS
            )
            if not matched:
                raise RestoreCliError(f"no supported files found in directory: {pattern}")
            resolved.extend(matched)
            continue
        if os.path.isfile(pattern):
            resolved.append(pattern)
            continue
        matched = sorted(p for p in glob.glob(pattern) if os.path.isfile(p))
        if not matched:
            raise RestoreCliError(f"input path did not match any file: {pattern}")
        resolved.extend(matched)

    seen: set[str] = set()
    unique: list[Path] = []
    for path in resolved:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        unique.append(Path(path))
    return unique


def validate_output_dir(out_dir: str, inputs: list[Path]) -> None:
    """出力先が入力を上書きしないことを検証する。"""
    out_real = os.path.realpath(out_dir)
    for path in inputs:
        in_dir = os.path.realpath(str(path.parent.absolute()))
        if in_dir == out_real:
            raise RestoreCliError(
                "output directory is the same as an input directory "
                f"({out_dir}); refusing to overwrite input files"
            )
    basenames: dict[str, Path] = {}
    for path in inputs:
        if path.name in basenames:
            raise RestoreCliError(
                f"two input files share the basename '{path.name}' "
                f"({basenames[path.name]} and {path}); outputs would collide"
            )
        basenames[path.name] = path


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------


def _print_counts(counts: dict[str, int], indent: str = "  ") -> None:
    if not counts:
        print(f"{indent}(置換なし)")
        return
    for key in sorted(counts, key=lambda k: (-counts[k], k)):
        print(f"{indent}{COUNT_LABELS.get(key, key):<22} {counts[key]}")


def _print_residuals(item: FileReport) -> None:
    """マッピングに無い仮名らしき文字列の警告。**値そのものは出さない。**"""
    if not item.residuals:
        return
    print(
        f"warning: {item.filename}: マッピングに無い仮名らしき文字列が "
        f"{item.residual_total} 件残っています",
        file=sys.stderr,
    )
    print(
        "warning:   マッピングが古いか、別環境のソルトで仮名化されたファイルの可能性があります。",
        file=sys.stderr,
    )
    for group in item.residuals:
        print(f"warning:   {group.describe()}", file=sys.stderr)


def print_report(report: RestoreReport, out_dir: str) -> None:
    for item in report.files:
        print(f"\n[{item.source_name}] -> {item.filename}")
        _print_counts(item.counts)
    print("\n置換件数の合計:")
    _print_counts(report.counts)
    print(f"\nrestored {len(report.files)} file(s) -> {out_dir}")
    for item in report.files:
        _print_residuals(item)
    print(
        "warning: 復元後のファイルは実名（AP名・サイト名・MAC・IP・実時刻）を含みます。"
        "取り扱いに注意してください。",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pseudonymize restore",
        description="仮名化したファイル（加工後でも可）を元の値に戻す。",
    )
    parser.add_argument("inputs", nargs="+", metavar="INPUT",
                        help="ファイル、ディレクトリ、または glob パターン")
    parser.add_argument("--out", required=True, metavar="DIR", help="出力先ディレクトリ")
    parser.add_argument("--no-time", action="store_true",
                        help="時刻を戻さない（識別子だけ戻す）")
    parser.add_argument("--salt-file", metavar="PATH",
                        help="ソルトファイル（既定: data/.pseudonym_salt.json）")
    parser.add_argument("--map-file", metavar="PATH",
                        help="マッピングファイル（既定: ソルトと同じディレクトリ）")
    return parser


def run(args: argparse.Namespace) -> int:
    inputs = resolve_inputs(args.inputs)
    validate_output_dir(args.out, inputs)

    map_path = args.map_file
    if map_path is None and args.salt_file is not None:
        from .salt import default_map_path

        map_path = default_map_path(args.salt_file)

    engine = load_engine(args.salt_file, map_path, time_restore=not args.no_time)

    out_dir = Path(args.out)
    report = RestoreReport()
    for path in inputs:
        report.files.append(engine.restore_file(path, out_dir))
    print_report(report, args.out)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (RestoreCliError, RestoreError, SaltError, PseudonymizeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

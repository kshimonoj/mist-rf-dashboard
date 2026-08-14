"""pseudonymize CLI — Mist Dashboard の CSV ログを一貫性のある仮名に置換する。

使用例:
    python -m pseudonymizer /app/data/logs --out ~/pseudo-logs
    python -m pseudonymizer 'logs/ap_metrics_*.csv' --out out --dry-run
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import os
import sys
from dataclasses import dataclass, field

from .leakcheck import LeakCheckFailed, check_output
from .salt import (
    SaltError,
    default_map_path,
    default_salt_path,
    generate_salt_material,
    load_or_create_salt,
)
from .schemas import FileType, TransformType as T, detect_file_type
from .transforms import (
    NUMBERED_TYPES,
    MappingStore,
    Pseudonymizer,
    PseudonymizeError,
    load_mapping,
    save_mapping,
)

UNKNOWN_ERROR = "error"
UNKNOWN_DROP = "drop"
UNKNOWN_KEEP = "keep"


class CliError(RuntimeError):
    """CLI レベルの致命的エラー。"""


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


@dataclass
class InputFile:
    path: str
    file_type: FileType
    header: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    output_columns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 入力の解決
# ---------------------------------------------------------------------------

def resolve_inputs(patterns: list[str]) -> list[str]:
    """ファイル・ディレクトリ・glob パターンを CSV ファイルのリストに展開する。"""
    resolved: list[str] = []
    for pattern in patterns:
        if os.path.isdir(pattern):
            matched = sorted(glob.glob(os.path.join(pattern, "*.csv")))
            if not matched:
                raise CliError(f"no .csv files found in directory: {pattern}")
            resolved.extend(matched)
            continue
        if os.path.isfile(pattern):
            resolved.append(pattern)
            continue
        matched = sorted(p for p in glob.glob(pattern) if os.path.isfile(p))
        if not matched:
            raise CliError(f"input path did not match any file: {pattern}")
        resolved.extend(matched)

    seen: set[str] = set()
    unique: list[str] = []
    for path in resolved:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        unique.append(path)
    return unique


def validate_output_dir(out_dir: str, inputs: list[str]) -> None:
    """出力先が入力を上書きしないことを検証する。"""
    out_real = os.path.realpath(out_dir)
    for path in inputs:
        in_dir = os.path.realpath(os.path.dirname(os.path.abspath(path)))
        if in_dir == out_real:
            raise CliError(
                "output directory is the same as an input directory "
                f"({out_dir}); refusing to overwrite input files"
            )
    basenames: dict[str, str] = {}
    for path in inputs:
        name = os.path.basename(path)
        if name in basenames:
            raise CliError(
                f"two input files share the basename '{name}' "
                f"({basenames[name]} and {path}); outputs would collide"
            )
        basenames[name] = path


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def read_input(path: str, unknown_mode: str) -> InputFile:
    filename = os.path.basename(path)
    file_type = detect_file_type(filename)
    if file_type is None:
        raise CliError(f"cannot determine file type from filename: {filename}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, restkey="__extra__", restval="")
        header = list(reader.fieldnames or [])
        if not header:
            raise CliError(f"input file has no header row: {path}")
        rows = []
        for i, row in enumerate(reader, start=2):
            if row.pop("__extra__", None):
                raise CliError(f"{path}: line {i} has more fields than the header")
            rows.append({k: ("" if v is None else v) for k, v in row.items()})

    if len(set(header)) != len(header):
        raise CliError(f"{path}: duplicated column name in header")

    unknown = [c for c in header if c not in file_type.whitelist]
    if unknown:
        if unknown_mode == UNKNOWN_ERROR:
            raise CliError(
                f"{path}: unknown column(s) not in the {file_type.key} whitelist: "
                f"{', '.join(unknown)}\n"
                "  --unknown-column drop で除外、keep でそのまま通せます（keep は危険です）。"
            )
        if unknown_mode == UNKNOWN_DROP:
            _warn(f"{path}: dropping unknown column(s): {', '.join(unknown)}")
        else:
            _warn(
                f"{path}: KEEPING unknown column(s) unmodified: {', '.join(unknown)}\n"
                "warning:   これらの列は仮名化されません。実データが出力に残る可能性があります。"
            )

    missing = [c for c in file_type.columns if c not in header]
    if missing:
        _warn(f"{path}: whitelisted column(s) absent from input: {', '.join(missing)}")

    if unknown_mode == UNKNOWN_DROP:
        output_columns = [c for c in header if c in file_type.whitelist]
    else:
        output_columns = list(header)

    return InputFile(
        path=path,
        file_type=file_type,
        header=header,
        rows=rows,
        unknown_columns=unknown,
        output_columns=output_columns,
    )


# ---------------------------------------------------------------------------
# 変換と検証
# ---------------------------------------------------------------------------

def transform_file(engine: Pseudonymizer, item: InputFile, unknown_mode: str) -> tuple[list[dict[str, str]], str]:
    """1 ファイルを仮名化して (行, CSV テキスト) を返す。まだ書き出さない。"""
    out_rows = []
    for row in item.rows:
        transformed = engine.transform_row(item.file_type, row)
        out_rows.append({c: transformed.get(c, "") for c in item.output_columns})

    kept = frozenset(item.unknown_columns) if unknown_mode == UNKNOWN_KEEP else frozenset()
    violations = check_output(
        item.output_columns,
        out_rows,
        allowed_columns=item.file_type.whitelist | kept,
        allowed_ips=frozenset(engine.generated_ips),
    )
    if violations:
        raise LeakCheckFailed(item.path, violations)

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=item.output_columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(out_rows)
    return out_rows, buf.getvalue()


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------

def print_dry_run(items: list[InputFile], engine: Pseudonymizer) -> None:
    print("dry-run: no files written.")
    for item in items:
        ft = item.file_type
        transformed_cols = []
        passthrough = 0
        for column in item.output_columns:
            rule = ft.rule_for(column)
            if rule is None:
                continue
            if rule is T.PASSTHROUGH:
                passthrough += 1
            else:
                transformed_cols.append((column, rule))
        print(f"\n[{ft.key}] {item.path}")
        print(f"  rows: {len(item.rows)}")
        print(
            f"  columns: {len(item.output_columns)} "
            f"(transform {len(transformed_cols)}, passthrough {passthrough}, "
            f"unknown {len(item.unknown_columns)})"
        )
        if item.unknown_columns:
            print(f"  unknown columns: {', '.join(item.unknown_columns)}")
        print("  transformed cells by column:")
        for column, rule in transformed_cols:
            count = sum(1 for row in item.rows if (row.get(column) or "").strip())
            print(f"    {column:<24} {rule.value:<14} {count}")

    print("\ndistinct values by transform type:")
    for ttype in sorted(NUMBERED_TYPES, key=lambda t: t.value):
        count = engine.stats.distinct_by_type.get(ttype, 0)
        if count:
            print(f"  {ttype.value:<14} {count}")
    print(f"\ntime offset: {engine.time_offset_seconds} seconds")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pseudonymize",
        description=(
            "Mist Dashboard の CSV ログを一貫性のある仮名に置換する"
            "（マスキングではなく仮名化）。"
        ),
    )
    parser.add_argument("inputs", nargs="+", metavar="INPUT",
                        help="ファイル、ディレクトリ、または glob パターン")
    parser.add_argument("--out", required=True, metavar="DIR", help="出力先ディレクトリ")
    parser.add_argument("--salt-file", metavar="PATH",
                        help="ソルトファイル（既定: <out>/.pseudonym_salt.json）")
    parser.add_argument("--unknown-column", choices=(UNKNOWN_ERROR, UNKNOWN_DROP, UNKNOWN_KEEP),
                        default=UNKNOWN_ERROR, help="ホワイトリスト外の列の扱い（既定: error）")
    parser.add_argument("--keep-vlan", action="store_true",
                        help="vlan_id を変換せず保持する")
    parser.add_argument("--no-time-shift", action="store_true",
                        help="タイムシフトを行わない（非推奨）")
    parser.add_argument("--dry-run", action="store_true",
                        help="出力せず、検出した種別・列・変換件数のみ表示する")
    return parser


def run(args: argparse.Namespace) -> int:
    inputs = resolve_inputs(args.inputs)
    out_dir = args.out
    validate_output_dir(out_dir, inputs)

    if args.no_time_shift:
        _warn(
            "--no-time-shift: タイムスタンプをそのまま出力します。"
            "日付は最も強い再識別の手がかりであり、この指定は推奨されません。"
        )

    salt_path = args.salt_file or default_salt_path(out_dir)

    if args.dry_run and not os.path.exists(salt_path):
        # dry-run で機密ファイルを作らない。この実行限りのソルトを使う。
        _warn(f"dry-run: salt file not found ({salt_path}); using an ephemeral salt")
        material = generate_salt_material()
        mapping = MappingStore(salt_fingerprint=material.fingerprint)
    else:
        material, _created = load_or_create_salt(salt_path)
        mapping = load_mapping(default_map_path(salt_path), material)

    engine = Pseudonymizer(
        material,
        mapping,
        keep_vlan=args.keep_vlan,
        time_shift=not args.no_time_shift,
        warn=_warn,
    )

    items = [read_input(path, args.unknown_column) for path in inputs]
    for item in items:
        for row in item.rows:
            engine.observe_row(item.file_type, row)
    engine.build()

    # 全ファイルを変換・検証してから書き出す（1 ファイルでも漏れたら何も書かない）
    outputs: list[tuple[str, str]] = []
    for item in items:
        _rows, text = transform_file(engine, item, args.unknown_column)
        outputs.append((os.path.basename(item.path), text))

    if args.dry_run:
        print_dry_run(items, engine)
        return 0

    os.makedirs(out_dir, exist_ok=True)
    for name, text in outputs:
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
            f.write(text)

    if mapping.dirty:
        save_mapping(default_map_path(salt_path), mapping)

    total_rows = sum(len(item.rows) for item in items)
    print(f"pseudonymized {len(outputs)} file(s), {total_rows} row(s) -> {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (CliError, SaltError, PseudonymizeError, LeakCheckFailed) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

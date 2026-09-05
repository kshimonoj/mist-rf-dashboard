"""report CLI のテスト。合成データのみを使う。

CLI と API が同じ関数（:mod:`report.analysis`）を通ることを担保する。
"""
from __future__ import annotations

import _repsynth as S
import pytest
from pptx import Presentation

from report import analysis, cli


@pytest.fixture
def data_dir(tmp_path):
    dirs = analysis.ResultsDirs.under(tmp_path)
    S.write_hangap(dirs.hangap)
    S.write_floorpeak(dirs.floorpeak)
    S.write_rrm(dirs.rrm)
    return tmp_path


def _run(data_dir, out, *args) -> int:
    return cli.main(["generate", "--data-dir", str(data_dir), "--out", str(out), *args])


def test_generate_writes_pptx(data_dir, tmp_path):
    out = tmp_path / "out"
    assert _run(data_dir, out, "--rrm", S.RRM_NAME) == cli.EXIT_OK
    written = list(out.glob("report_*.pptx"))
    assert len(written) == 1
    assert len(Presentation(str(written[0])).slides) == 1 + 3


def test_generate_without_any_selection_is_input_error(data_dir, tmp_path, capsys):
    assert _run(data_dir, tmp_path / "out") == cli.EXIT_INPUT_ERROR
    assert "選ばれていません" in capsys.readouterr().err


def test_missing_result_is_input_error(data_dir, tmp_path, capsys):
    code = _run(data_dir, tmp_path / "out", "--rrm", "rrm_result_20991231_235959")
    assert code == cli.EXIT_INPUT_ERROR
    assert "見つかりません" in capsys.readouterr().err


def test_missing_data_dir_is_input_error(tmp_path, capsys):
    code = _run(tmp_path / "nope", tmp_path / "out", "--rrm", S.RRM_NAME)
    assert code == cli.EXIT_INPUT_ERROR
    assert "--data-dir" in capsys.readouterr().err


def test_section_order_is_printed_fixed(data_dir, tmp_path, capsys):
    """引数の並びを変えても、出力される章の順序は固定。"""
    assert _run(
        data_dir, tmp_path / "out",
        "--rrm", S.RRM_NAME, "--floorpeak", S.FLOORPEAK_NAME, "--hangap", S.HANGAP_NAME,
    ) == cli.EXIT_OK
    printed = capsys.readouterr().out
    assert printed.index("Hang AP") < printed.index("Floor Peak") < printed.index("RRM")


def test_unknown_option_is_input_error(data_dir, tmp_path):
    assert _run(data_dir, tmp_path / "out", "--nope", "x") == cli.EXIT_INPUT_ERROR

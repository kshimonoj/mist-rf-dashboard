"""leak check の 5 規則が発火すること、および値を漏らさないこと。"""
import csv

from conftest import read_csv, write_csv

from pseudonymizer.cli import main
from pseudonymizer.leakcheck import (
    RULE_MAC,
    RULE_NON_ASCII,
    RULE_PRIVATE_IP,
    RULE_UNKNOWN_COLUMN,
    RULE_UUID,
    LeakCheckFailed,
    check_output,
)
from pseudonymizer.schemas import AP_EVENTS_COLUMNS

WHITELIST = frozenset(AP_EVENTS_COLUMNS)
NO_IPS = frozenset()


def _rules(header, rows, allowed=WHITELIST, allowed_ips=NO_IPS):
    return {v.rule for v in check_output(header, rows,
                                         allowed_columns=allowed, allowed_ips=allowed_ips)}


def test_rule_uuid_fires_on_unconverted_uuid():
    rows = [{"reason": "123e4567-e89b-42d3-a456-426614174000"}]
    assert RULE_UUID in _rules(["reason"], rows)


def test_rule_uuid_allows_generated_pseudonym_uuid():
    rows = [{"reason": "10000000-0000-4000-8000-000000000042"},
            {"reason": "20000000-0000-4000-8000-000000000007"},
            {"reason": "30000000-0000-4000-8000-000000000001"}]
    assert RULE_UUID not in _rules(["reason"], rows)


def test_rule_uuid_allows_generated_map_id_pseudonym():
    """MAP_ID の仮名 UUID(30000000- プレフィックス)を自分の漏れとして誤検出しない。"""
    rows = [{"reason": "30000000-0000-4000-8000-000000000123"}]
    assert RULE_UUID not in _rules(["reason"], rows)


def test_rule_mac_fires_on_non_02_prefixed_mac():
    assert RULE_MAC in _rules(["ap_mac"], [{"ap_mac": "aabbccddeeff"}])
    assert RULE_MAC in _rules(["ap_mac"], [{"ap_mac": "aa:bb:cc:dd:ee:ff"}])


def test_rule_mac_allows_generated_mac():
    assert RULE_MAC not in _rules(["ap_mac"], [{"ap_mac": "0200000000c8"}])
    assert RULE_MAC not in _rules(["ap_mac"], [{"ap_mac": "0210000000c8"}])


def test_rule_mac_does_not_fire_on_large_decimal_counters():
    """tx_bytes のような 12 桁の 10 進数を MAC と誤認しない。"""
    assert RULE_MAC not in _rules(["reason"], [{"reason": "123456789012"}])


def test_rule_private_ip_fires():
    assert RULE_PRIVATE_IP in _rules(["reason"], [{"reason": "192.168.10.5"}])
    assert RULE_PRIVATE_IP in _rules(["reason"], [{"reason": "172.20.1.1"}])
    assert RULE_PRIVATE_IP in _rules(["reason"], [{"reason": "10.99.99.99"}])


def test_rule_private_ip_allows_generated_ip():
    assert RULE_PRIVATE_IP not in _rules(["reason"], [{"reason": "10.0.0.1"}],
                                         allowed_ips=frozenset({"10.0.0.1"}))


def test_rule_unknown_column_fires():
    assert RULE_UNKNOWN_COLUMN in _rules(["reason", "customer_note"],
                                         [{"reason": "ok", "customer_note": "ok"}])


def test_rule_non_ascii_fires():
    assert RULE_NON_ASCII in _rules(["reason"], [{"reason": "テスト棟 1F"}])


def test_all_five_rules_can_fire_together():
    header = ["reason", "ap_mac", "ip_note", "extra_col", "jp_col"]
    rows = [{
        "reason": "123e4567-e89b-42d3-a456-426614174000",
        "ap_mac": "aabbccddeeff",
        "ip_note": "192.168.0.1",
        "extra_col": "x",
        "jp_col": "テスト",
    }]
    fired = _rules(header, rows, allowed=frozenset({"reason", "ap_mac", "ip_note", "jp_col"}))
    assert fired == {RULE_UUID, RULE_MAC, RULE_PRIVATE_IP, RULE_UNKNOWN_COLUMN, RULE_NON_ASCII}


def test_clean_output_has_no_violations(indir, tmp_path):
    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out)]) == 0
    # 正常出力そのものを再検査しても違反ゼロであること
    from pseudonymizer.schemas import detect_file_type
    for path in sorted(out.glob("*.csv")):
        rows = read_csv(path)
        with open(path, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        ft = detect_file_type(header)
        ips = {r.get("ip") for r in rows if r.get("ip")}
        assert not check_output(header, rows, allowed_columns=ft.whitelist,
                                allowed_ips=frozenset(ips))


def test_error_message_never_contains_the_leaked_value():
    secret_uuid = "123e4567-e89b-42d3-a456-426614174000"
    secret_mac = "aabbccddeeff"
    secret_ip = "192.168.10.5"
    secret_jp = "テスト棟 1F"
    header = ["reason", "ap_mac", "band", "channel"]
    rows = [{"reason": secret_uuid, "ap_mac": secret_mac,
             "band": secret_ip, "channel": secret_jp}]
    violations = check_output(header, rows, allowed_columns=WHITELIST, allowed_ips=NO_IPS)
    assert violations

    message = str(LeakCheckFailed("out/ap_events_x.csv", violations))
    for secret in (secret_uuid, secret_mac, secret_ip, secret_jp):
        assert secret not in message
    # 列名・行番号・規則名は含まれる
    assert "ap_mac" in message and "line=2" in message and RULE_MAC in message


def test_cli_aborts_and_writes_nothing_when_a_leak_is_detected(indir, tmp_path, capsys):
    """未知列を keep で通し、その中に実在 OUI の MAC を残したら出力を破棄する。"""
    path = indir / "ap_events_20240101_0900_TZT.csv"
    rows = read_csv(path)
    header = list(AP_EVENTS_COLUMNS) + ["legacy_mac"]
    for r in rows:
        r["legacy_mac"] = "aabbccddeeff"
    write_csv(path, header, rows)

    out = tmp_path / "out"
    assert main([str(indir), "--out", str(out), "--unknown-column", "keep"]) == 1
    captured = capsys.readouterr()
    assert "leak check failed" in captured.err
    assert "aabbccddeeff" not in captured.err
    # 1 ファイルでも漏れたら、他のファイルも書き出さない
    assert not list(out.glob("*.csv"))

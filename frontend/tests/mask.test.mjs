/**
 * デモ用マスク（src/lib/mask.ts）の単体テスト。
 *
 * フロントにテスト基盤が無いので、**新しい依存は足さず** Node 標準の
 * `node --test` と型ストリップ（Node 22.6+）だけで動かす。
 *
 *   cd frontend && npm test
 *
 * 変換は純関数として切り出してあるので、UI を起動せずにここだけを検証できる。
 */
import assert from "node:assert/strict";
import { test, beforeEach } from "node:test";

import {
  createRegistry,
  useRegistry,
  setMaskEnabled,
  maskPayload,
  maskResponse,
  maskScalar,
  scrubText,
  resolveKind,
  DEMO_ADDRESS,
  downloadsDisabled,
  floorMapBlocked,
  prefetchForMask,
} from "../src/lib/mask.ts";

/** 各テストを独立したレジストリで始める（採番の持ち越しを避ける） */
function reset() {
  useRegistry(createRegistry());
}

beforeEach(reset);

// ---------------------------------------------------------------------------
// 1. 種別ごとの書式
// ---------------------------------------------------------------------------

test("AP名は AP-001 形式の連番になる", () => {
  const out = maskPayload([{ ap_name: "STASW-05F-AP63E-0190" }, { ap_name: "STASW-05F-AP63E-0191" }]);
  const names = out.map((r) => r.ap_name).sort();
  assert.deepEqual(names, ["AP-001", "AP-002"]);
});

test("サイト名は Site A / Site B になり、住所は固定の架空値になる", () => {
  const out = maskPayload([
    { id: "s1", name: "1_Kyobashi", address: "東京都中央区京橋1-2-3", ap_count: 3 },
    { id: "s2", name: "2_Osaka", address: "大阪府大阪市北区4-5-6", ap_count: 1 },
  ]);
  const names = out.map((r) => r.name).sort();
  assert.deepEqual(names, ["Site A", "Site B"]);
  assert.equal(out[0].address, DEMO_ADDRESS);
  assert.equal(out[1].address, DEMO_ADDRESS);
});

test("フロア名 / マップ名は Floor 1 形式になる", () => {
  const out = maskPayload([{ map_name: "10_STA-05F-Floorplan" }, { map_name: "11_STA-06F-Floorplan" }]);
  const names = out.map((r) => r.map_name).sort();
  assert.deepEqual(names, ["Floor 1", "Floor 2"]);
});

test("ホスト名は Client-001 形式になる", () => {
  const out = maskPayload({ hostname: "kshimono-mbp" });
  assert.equal(out.hostname, "Client-001");
});

test("MAC はローカル管理アドレス 02:f… になる", () => {
  const out = maskPayload({ mac: "a8:f7:d9:81:e2:da" });
  assert.equal(out.mac, "02:f0:00:00:00:01");
});

test("IP は RFC 5737 のドキュメント用アドレスになる", () => {
  const out = maskPayload({ ip: "10.1.2.3" });
  assert.match(out.ip, /^192\.0\.2\.\d+$/);
});

test("シリアルは同じ桁数の英数字になる", () => {
  assert.equal(maskScalar("serial", "A1B2C3D4E5F6"), "DEMO00000001");
  assert.equal(maskScalar("serial", "A1B2C3D4E5F6").length, "A1B2C3D4E5F6".length);
});

test("SSID / Device Profile / RF Template / タグ / 環境名は CSV 仮名化と違う形になる", () => {
  assert.equal(maskScalar("ssid", "kyobashi-guest"), "SSID-001"); // CSV は SSID_001
  assert.equal(maskScalar("profileName", "Kyobashi-DP"), "Profile-001");
  assert.equal(maskScalar("rfTemplateName", "Kyobashi-RF"), "RF-Template-001");
  assert.equal(maskScalar("tag", "京橋3F"), "Tag-001");
  assert.equal(maskScalar("envName", "A社本番"), "Env-001");
});

test("CSV 仮名化（AP_0001 / SITE_001 / FLOOR_001 / HOST_0001）と同じ形にならない", () => {
  const out = maskPayload({ ap_name: "x", site_name: "y", map_name: "z", hostname: "w" });
  assert.doesNotMatch(out.ap_name, /^AP_\d+$/);
  assert.doesNotMatch(out.site_name, /^SITE_\d+$/);
  assert.doesNotMatch(out.map_name, /^FLOOR_\d+$/);
  assert.doesNotMatch(out.hostname, /^HOST_\d+$/);
});

// ---------------------------------------------------------------------------
// 2. 決定論性（同じ入力 → 常に同じ出力）
// ---------------------------------------------------------------------------

test("同じ入力は常に同じ出力になる（同一レジストリ内で繰り返し）", () => {
  const payload = { ap_name: "AP-西館-01", site_name: "1_Kyobashi", mac: "a8:f7:d9:81:e2:da" };
  const a = maskPayload(payload);
  const b = maskPayload(payload);
  assert.deepEqual(a, b);
});

test("レジストリを作り直しても同じ入力なら同じ出力になる", () => {
  const payload = [
    { ap_name: "AP-A", mac: "aa:bb:cc:dd:ee:01", ip: "10.0.0.1" },
    { ap_name: "AP-B", mac: "aa:bb:cc:dd:ee:02", ip: "10.0.0.2" },
    { ap_name: "AP-C", mac: "aa:bb:cc:dd:ee:03", ip: "10.0.0.3" },
  ];
  const first = maskPayload(payload);
  reset();
  const second = maskPayload(payload);
  assert.deepEqual(first, second);
});

test("配列の並び順が変わっても同じ実名には同じ仮名が付く", () => {
  const rows = [{ ap_name: "AP-A" }, { ap_name: "AP-B" }, { ap_name: "AP-C" }];
  const forward = maskPayload(rows);
  const map = new Map(rows.map((r, i) => [r.ap_name, forward[i].ap_name]));
  reset();
  const reversed = maskPayload([...rows].reverse());
  for (let i = 0; i < reversed.length; i++) {
    const real = [...rows].reverse()[i].ap_name;
    assert.equal(reversed[i].ap_name, map.get(real));
  }
});

// ---------------------------------------------------------------------------
// 3. 衝突しないこと
// ---------------------------------------------------------------------------

test("数百件の異なる入力が、すべて異なる出力になる", () => {
  const rows = [];
  for (let i = 0; i < 500; i++) {
    rows.push({
      ap_name: `STASW-05F-AP63E-${String(i).padStart(4, "0")}`,
      mac: `a8:f7:d9:${String(i % 256).padStart(2, "0")}:${String((i >> 8) % 256).padStart(2, "0")}:01`,
      hostname: `host-${i}`,
      site_name: `site-${i}`,
      map_name: `floor-${i}`,
    });
  }
  const out = maskPayload(rows);
  for (const key of ["ap_name", "hostname", "site_name", "map_name"]) {
    assert.equal(new Set(out.map((r) => r[key])).size, 500, `${key} が衝突した`);
  }
  // mac は入力側に重複があるので、実名の異なり数と一致していればよい
  const distinctMacs = new Set(rows.map((r) => r.mac)).size;
  assert.equal(new Set(out.map((r) => r.mac)).size, distinctMacs);
});

// ---------------------------------------------------------------------------
// 4. ネストしたオブジェクト・配列
// ---------------------------------------------------------------------------

test("ネストしたオブジェクト・配列の中の値も変換される", () => {
  const payload = {
    summary: {
      loader: {
        site_periods: [{ site_id: "uuid-1", site_name: "1_Kyobashi", rows: 10 }],
      },
    },
    recommendations: [{ ap_name: "AP-西館-01", actions: ["ch変更"], site_name: "1_Kyobashi" }],
  };
  const out = maskPayload(payload);
  assert.equal(out.summary.loader.site_periods[0].site_name, "Site A");
  assert.equal(out.recommendations[0].site_name, "Site A");
  assert.equal(out.recommendations[0].ap_name, "AP-001");
  // 元データは壊さない
  assert.equal(payload.summary.loader.site_periods[0].site_name, "1_Kyobashi");
});

test("周辺AP名（カンマ区切りの一覧）は要素ごとに変換され、件数と区切りが保たれる", () => {
  const out = maskPayload({ ap_name: "AP-X", "周辺AP名": "AP-A, AP-B, AP-C" });
  const parts = out["周辺AP名"].split(", ");
  assert.equal(parts.length, 3);
  for (const p of parts) assert.match(p, /^AP-\d{3}$/);
  assert.equal(new Set(parts).size, 3);
  // 一覧の中の AP も、単独で出てくる AP と同じ番号空間で採番される
  assert.ok(!parts.includes(out.ap_name));
});

test("metrics_json（JSON 文字列）の中の ap_name も変換される", () => {
  const out = maskPayload({ metrics_json: '{"avg_rssi":-70,"ap_name":"AP-西館-01"}' });
  const parsed = JSON.parse(out.metrics_json);
  assert.equal(parsed.ap_name, "AP-001");
  assert.equal(parsed.avg_rssi, -70);
});

// ---------------------------------------------------------------------------
// 5. 対象外のフィールド
// ---------------------------------------------------------------------------

test("数値・タイムスタンプ・ID・ファイル名は変換されない", () => {
  const payload = {
    id: "00000000-1111-2222-3333-444455556666",
    site_id: "aaaa-bbbb",
    ap_id: "cccc-dddd",
    target_id: "eeee-ffff",
    timestamp: "2026-08-19T03:00:00Z",
    saved_at: "2026-08-19T03:00:00Z",
    num_clients: 42,
    channel: 36,
    model: "AP45",
    status: "connected",
    filename: "ap_metrics_20260819_1200_JST.csv",
    country_code: "JP",
    vlan_id: 100,
  };
  const out = maskPayload(payload);
  assert.deepEqual(out, payload);
});

test("保存済み結果の name（ダウンロード・削除のキー）は変換されない", () => {
  const saved = maskPayload({
    name: "hangap_result_20260819_120000",
    saved_at: "2026-08-19T03:00:00Z",
    files: { csv: 100 },
    total_bytes: 100,
  });
  assert.equal(saved.name, "hangap_result_20260819_120000");

  const page = maskPayload({ job_id: null, name: "hangap_result_20260819_120000", columns: [], rows: [] });
  assert.equal(page.name, "hangap_result_20260819_120000");
});

test("column_kinds（キーが列名の入れ物）の値は壊さない", () => {
  const out = maskPayload({
    columns: ["ap_name", "site_name"],
    column_kinds: { ap_name: "text", site_name: "text" },
    rows: [{ ap_name: "AP-西館-01", site_name: "1_Kyobashi" }],
  });
  assert.deepEqual(out.column_kinds, { ap_name: "text", site_name: "text" });
  assert.deepEqual(out.columns, ["ap_name", "site_name"]);
  assert.equal(out.rows[0].ap_name, "AP-001");
  assert.equal(out.rows[0].site_name, "Site A");
});

// ---------------------------------------------------------------------------
// 6. トグル OFF
// ---------------------------------------------------------------------------

test("トグル OFF のときは入力がそのまま返る", () => {
  setMaskEnabled(false);
  const payload = { ap_name: "STASW-05F-AP63E-0190", mac: "a8:f7:d9:81:e2:da" };
  assert.equal(maskResponse(payload), payload); // 同一オブジェクトがそのまま返る
});

test("トグル ON のときだけ変換される", () => {
  setMaskEnabled(true);
  try {
    const out = maskResponse({ ap_name: "STASW-05F-AP63E-0190" });
    assert.equal(out.ap_name, "AP-001");
  } finally {
    setMaskEnabled(false);
  }
});

// ---------------------------------------------------------------------------
// 7. 桁数・区切りの保持
// ---------------------------------------------------------------------------

test("MAC の桁数と区切りが保たれる", () => {
  assert.equal(maskScalar("mac", "a8:f7:d9:81:e2:da"), "02:f0:00:00:00:01");
  assert.equal(maskScalar("mac", "a8-f7-d9-81-e2-da"), "02-f0-00-00-00-01"); // 同じ AP → 同じ番号
  assert.equal(maskScalar("mac", "a8f7d981e2da"), "02f000000001");
  assert.equal(maskScalar("mac", "A8:F7:D9:81:E2:DA"), "02:F0:00:00:00:01"); // 大文字も維持
  assert.equal(maskScalar("mac", "a8f7.d981.e2da"), "02f0.0000.0001");
});

test("IP はドット区切り 4 オクテットのまま", () => {
  const masked = maskScalar("ip", "10.1.2.3");
  assert.match(masked, /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/);
});

test("すでにマスク済みの値を二重に変換しない", () => {
  assert.equal(maskScalar("mac", "02:f0:00:00:00:07"), "02:f0:00:00:00:07");
  assert.equal(maskScalar("ip", "192.0.2.11"), "192.0.2.11");
});

// ---------------------------------------------------------------------------
// 8. 自由文
// ---------------------------------------------------------------------------

test("分析条件の 対象サイト= はレジストリに無くても落ちる", () => {
  const out = scrubText("分析条件: 対象サイト=1_Kyobashi [abc-123] / 窓 2026-08-19 〜 / gap_factor=1.5");
  assert.ok(!out.includes("1_Kyobashi"), out);
  assert.ok(out.includes("Site A [abc-123]"), out);
  assert.ok(out.includes("gap_factor=1.5"), out);
});

test("対象サイト=すべて はそのまま残る", () => {
  const out = scrubText("分析条件: 対象サイト=すべて / 窓 なし");
  assert.ok(out.includes("対象サイト=すべて"), out);
});

test("同じ応答に入っている実名は、自由文からも落ちる", () => {
  const out = maskPayload({
    site_name: "1_Kyobashi",
    recommendations: [{ ap_name: "STASW-05F-AP63E-0190" }],
    detail: "RSSI -70dBm (1h平均) で STASW-05F-AP63E-0190 に接続継続（1_Kyobashi）",
  });
  assert.ok(!out.detail.includes("STASW-05F-AP63E-0190"), out.detail);
  assert.ok(!out.detail.includes("1_Kyobashi"), out.detail);
  assert.ok(out.detail.includes("AP-001"), out.detail);
  assert.ok(out.detail.includes("-70dBm"), out.detail);
});

test("自由文の中の MAC / IP も落ちる", () => {
  const out = scrubText("AP a8:f7:d9:81:e2:da (10.1.2.3) が応答しません");
  assert.ok(!out.includes("a8:f7:d9:81:e2:da"), out);
  assert.ok(!out.includes("10.1.2.3"), out);
});

test("自由文の中の UUID は MAC と誤認されない", () => {
  const uuid = "20000000-0000-4000-8000-000000000001";
  assert.equal(scrubText(`site_id=${uuid}`), `site_id=${uuid}`);
});

// ---------------------------------------------------------------------------
// 9. フィールド名の判定
// ---------------------------------------------------------------------------

test("name の種別は同じオブジェクトの他のキーで決まる", () => {
  assert.equal(resolveKind("name", { id: "x", name: "y", mac: "z" }), "apName");
  assert.equal(resolveKind("name", { id: "x", name: "y", width: 100 }), "floorName");
  assert.equal(resolveKind("name", { id: "x", name: "y", mist_org_id: "o" }), "envName");
  assert.equal(resolveKind("name", { id: "x", name: "y", ap_count: 3 }), "siteName");
  assert.equal(resolveKind("name", { id: "x", name: "y" }), "siteName");
  assert.equal(resolveKind("name", { name: "y", saved_at: null, files: {} }), null);
});

test("target_name の種別は target_type で決まる", () => {
  const ap = maskPayload({ target_type: "ap", target_name: "STASW-AP-01" });
  assert.equal(ap.target_name, "AP-001");

  reset();
  const pair = maskPayload({ target_type: "ap_pair", target_name: "AP-A ↔ AP-B" });
  assert.equal(pair.target_name, "AP-001 ↔ AP-002");

  reset();
  const client = maskPayload({ target_type: "client", target_name: "kshimono-mbp" });
  assert.equal(client.target_name, "Client-001");
});

// ---------------------------------------------------------------------------
// 10. ダウンロード / Floor Map の一律無効化（29番: マスク中の事故防止）
// ---------------------------------------------------------------------------

test("マスク ON のときダウンロードは無効化され、OFF のときは無効化されない", () => {
  setMaskEnabled(true);
  try {
    assert.equal(downloadsDisabled(), true);
  } finally {
    setMaskEnabled(false);
  }
  assert.equal(downloadsDisabled(), false);
});

test("マスク ON のとき Floor Map は表示をブロックされ、OFF のときはブロックされない", () => {
  setMaskEnabled(true);
  try {
    assert.equal(floorMapBlocked(), true);
  } finally {
    setMaskEnabled(false);
  }
  assert.equal(floorMapBlocked(), false);
});

// ---------------------------------------------------------------------------
// 11. 自由文の取りこぼし対策: AP 名の先行取得（29番）
// ---------------------------------------------------------------------------

test("先行取得（別応答）で採番済みの AP 名は、その後の自由文からも落ちる", () => {
  // Hang AP / Floor Peak の「サイト選択時に AP 一覧を先行取得する」を模する:
  // 1 つ目の応答（AP 一覧）で ap_name を採番したあと、2 つ目の応答（警告文）の中の
  // 同じ AP 名も置換されることを確認する。
  maskPayload([{ ap_name: "STASW-05F-AP63E-0190", mac: "a8:f7:d9:81:e2:da" }]);
  const out = maskPayload({
    warnings: ["STASW-05F-AP63E-0190 の応答が途絶えています"],
  });
  assert.ok(!out.warnings[0].includes("STASW-05F-AP63E-0190"), out.warnings[0]);
  assert.ok(out.warnings[0].includes("AP-001"), out.warnings[0]);
});

test("既知の限界: 先行取得していない AP 名は自由文にそのまま残る", () => {
  // 一覧に含まれない AP（撤去済み等）の名前は、警告文の中だけに出てきても落とせない。
  // 挙動が変わったら気づけるよう、ここで固定する。
  const out = scrubText("STASW-05F-AP63E-9999 の応答が途絶えています");
  assert.ok(out.includes("STASW-05F-AP63E-9999"), out);
});

test("prefetchForMask はマスク OFF のとき取得関数を呼ばない", async () => {
  setMaskEnabled(false);
  let called = false;
  await prefetchForMask(async () => { called = true; });
  assert.equal(called, false);
});

test("prefetchForMask はマスク ON のとき取得関数を呼び、失敗しても投げない", async () => {
  setMaskEnabled(true);
  try {
    let called = false;
    await prefetchForMask(async () => { called = true; });
    assert.equal(called, true);

    await assert.doesNotReject(prefetchForMask(async () => { throw new Error("network error"); }));
  } finally {
    setMaskEnabled(false);
  }
});

test("ホスト名の欄に MAC が入っていれば MAC として変換する", () => {
  const out = maskPayload({ target_type: "client", target_name: "a8:f7:d9:81:e2:da" });
  assert.equal(out.target_name, "02:f0:00:00:00:01");
});

test("API トークンは桁数を保って伏せられる", () => {
  const out = maskPayload({ mist_api_token: "abcdefghij", mist_org_id: "org-uuid" });
  assert.equal(out.mist_api_token, "**********");
  assert.equal(out.mist_org_id, "org-uuid");
});

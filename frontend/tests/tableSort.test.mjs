/**
 * 明細テーブルの列ソート・列フィルタ（src/lib/tableSort.ts）の単体テスト。
 *
 * DataTable コンポーネント（React / jsdom が要る）ではなく、切り出した純関数だけを
 * `node --test` で検証する（フロントにテスト基盤が無いため。mask.test.mjs と同じ方針）。
 *
 *   cd frontend && npm test
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyColumnFilters,
  compareForSort,
  isFilterActive,
  matchesFilter,
  sortByColumn,
} from "../src/lib/tableSort.ts";

// ---------------------------------------------------------------------------
// 1. ソート: 列ヘッダークリックの昇順・降順（sortByColumn を asc/desc 両方向で確認）
// ---------------------------------------------------------------------------

test("文字列列は昇順・降順を切り替えられる", () => {
  const rows = [{ name: "banana" }, { name: "apple" }, { name: "cherry" }];
  const asc = sortByColumn(rows, (r) => r.name, "text", "asc").map((r) => r.name);
  assert.deepEqual(asc, ["apple", "banana", "cherry"]);
  const desc = sortByColumn(rows, (r) => r.name, "text", "desc").map((r) => r.name);
  assert.deepEqual(desc, ["cherry", "banana", "apple"]);
});

// ---------------------------------------------------------------------------
// 2. 数値列は数値として比較する（文字列の辞書順にならないこと。"2" が "10" より前に来る）
// ---------------------------------------------------------------------------

test("数値列は数値として比較される（2 が 10 より前）", () => {
  const rows = [{ n: 10 }, { n: 2 }, { n: 1 }];
  const asc = sortByColumn(rows, (r) => r.n, "number", "asc").map((r) => r.n);
  assert.deepEqual(asc, [1, 2, 10]);
});

test("数値列は文字列で来ても数値として比較される", () => {
  const rows = [{ n: "10" }, { n: "2" }, { n: "1" }];
  const asc = sortByColumn(rows, (r) => r.n, "number", "asc").map((r) => r.n);
  assert.deepEqual(asc, ["1", "2", "10"]);
});

test("time 列は日時として比較される", () => {
  const rows = [
    { t: "2026-01-01 10:00:00" },
    { t: "2026-01-01 09:00:00" },
    { t: "2026-01-02 08:00:00" },
  ];
  const asc = sortByColumn(rows, (r) => r.t, "time", "asc").map((r) => r.t);
  assert.deepEqual(asc, ["2026-01-01 09:00:00", "2026-01-01 10:00:00", "2026-01-02 08:00:00"]);
});

test("値の無い行は昇順でも降順でも常に末尾", () => {
  const rows = [{ n: 5 }, { n: null }, { n: 1 }, { n: undefined }];
  const asc = sortByColumn(rows, (r) => r.n, "number", "asc").map((r) => r.n);
  assert.deepEqual(asc, [1, 5, null, undefined]);
  const desc = sortByColumn(rows, (r) => r.n, "number", "desc").map((r) => r.n);
  assert.deepEqual(desc, [5, 1, null, undefined]);
});

test("compareForSort: bool は false < true", () => {
  assert.equal(compareForSort(false, true, "bool") < 0, true);
  assert.equal(compareForSort(true, false, "bool") > 0, true);
  assert.equal(compareForSort(true, true, "bool"), 0);
});

// ---------------------------------------------------------------------------
// 3. フィルタは表示専用（元の配列を変更しない = ダウンロード内容に影響しない）
// ---------------------------------------------------------------------------

test("applyColumnFilters は元の配列を変更しない", () => {
  const rows = [{ v: 1 }, { v: 2 }, { v: 3 }];
  const before = JSON.stringify(rows);
  applyColumnFilters(rows, (r, key) => r[key], { v: { kind: "number", min: 2, max: null } });
  assert.equal(JSON.stringify(rows), before);
});

test("applyColumnFilters: 数値レンジで絞り込む", () => {
  const rows = [{ v: 1 }, { v: 5 }, { v: 10 }];
  const out = applyColumnFilters(rows, (r, key) => r[key], {
    v: { kind: "number", min: 2, max: 9 },
  });
  assert.deepEqual(out.map((r) => r.v), [5]);
});

test("applyColumnFilters: テキスト部分一致（大文字小文字を無視）", () => {
  const rows = [{ name: "TEST-AP-01" }, { name: "OTHER-AP" }];
  const out = applyColumnFilters(rows, (r, key) => r[key], {
    name: { kind: "text", value: "test" },
  });
  assert.deepEqual(out.map((r) => r.name), ["TEST-AP-01"]);
});

test("applyColumnFilters: enum は除外リストに無い値だけ残す", () => {
  const rows = [{ status: "ok" }, { status: "no_before" }, { status: "too_far" }];
  const out = applyColumnFilters(rows, (r, key) => r[key], {
    status: { kind: "enum", excluded: new Set(["no_before"]) },
  });
  assert.deepEqual(out.map((r) => r.status), ["ok", "too_far"]);
});

test("applyColumnFilters: bool フィルタ", () => {
  const rows = [{ flag: true }, { flag: false }, { flag: true }];
  const out = applyColumnFilters(rows, (r, key) => r[key], {
    flag: { kind: "bool", value: "true" },
  });
  assert.equal(out.length, 2);
});

// ---------------------------------------------------------------------------
// 4. 既存の専用フィルタ（例: 汚染・照合不可のみ）と汎用フィルタが同時に効くこと
// ---------------------------------------------------------------------------

test("専用フィルタ（配列の事前絞り込み）と汎用フィルタ（applyColumnFilters）は組み合わせて使える", () => {
  const allRows = [
    { ap_name: "AP-01", contaminated: true, clients: 5 },
    { ap_name: "AP-02", contaminated: true, clients: 20 },
    { ap_name: "AP-03", contaminated: false, clients: 3 },
  ];
  // 専用フィルタ: 「汚染の行だけ」（呼び出し側が事前に配列を絞る、DetailTable と同じやり方）
  const onlyContaminated = allRows.filter((r) => r.contaminated);
  // 汎用フィルタ: 汚染の行の中からさらに clients >= 10 だけ
  const out = applyColumnFilters(onlyContaminated, (r, key) => r[key], {
    clients: { kind: "number", min: 10, max: null },
  });
  assert.deepEqual(out.map((r) => r.ap_name), ["AP-02"]);
});

// ---------------------------------------------------------------------------
// isFilterActive / matchesFilter の基本挙動
// ---------------------------------------------------------------------------

test("isFilterActive: 空のフィルタは非アクティブ", () => {
  assert.equal(isFilterActive(undefined), false);
  assert.equal(isFilterActive({ kind: "text", value: "" }), false);
  assert.equal(isFilterActive({ kind: "number", min: null, max: null }), false);
  assert.equal(isFilterActive({ kind: "bool", value: "all" }), false);
  assert.equal(isFilterActive({ kind: "enum", excluded: new Set() }), false);
});

test("matchesFilter: 数値レンジ未指定の行は数値フィルタが無いときだけ通る", () => {
  assert.equal(matchesFilter(null, { kind: "number", min: null, max: null }), true);
  assert.equal(matchesFilter(null, { kind: "number", min: 1, max: null }), false);
});

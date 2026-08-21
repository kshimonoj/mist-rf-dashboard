/**
 * デモ用の画面匿名化（表示層のみ）。
 *
 * 設計方針:
 * - **バックエンドは一切変更しない。** API から受け取った JSON を、フィールド名の
 *   ルールで再帰的に置き換えてから画面へ渡す。変換は `lib/api.ts` の 1 箇所で挟む
 *   （各コンポーネントで個別に変換すると、画面を足すたびに書き忘れる余地が残る）。
 * - **元データは壊さない。** 受け取ったオブジェクトは変更せず、常に新しい値を作る。
 *   トグルを OFF にすれば変換関数を通さないので、即座に実名へ戻る。
 * - **同じ実名は常に同じ架空名になる。** 採番結果は localStorage に持ち越すので、
 *   リロード・画面移動・翌日の再訪でも同じ番号になる。1 つの応答の中で新しく出てきた
 *   値は、**実名のハッシュ順**に並べてから連番を配る（配列の並び順や表の並べ替えで
 *   番号が変わらないようにするため）。
 * - **CSV 仮名化（backend/pseudonymizer）とは番号が一致しない。** 混同を避けるため、
 *   画面側は CSV 側（`AP_0001` / `SITE_001` / `FLOOR_001` / `HOST_0001` / `SSID_001`、
 *   MAC は `020…` `021…`）と**別の形**にしている（`AP-001` / `Site A` / `Floor 1` /
 *   `Client-001` / `SSID-001`、MAC は `02:f0:…`）。
 *
 * ここは React に依存しない純関数の集まりにしてある（tests/mask.test.mjs から直接呼ぶ）。
 */

// ---------------------------------------------------------------------------
// 種別
// ---------------------------------------------------------------------------

export type MaskKind =
  | "apName"
  | "apNameList"
  | "apPairName"
  | "siteName"
  | "siteLabel"
  | "address"
  | "floorName"
  | "clientName"
  | "mac"
  | "ip"
  | "serial"
  | "ssid"
  | "profileName"
  | "rfTemplateName"
  | "envName"
  | "tag"
  | "token"
  | "freeText"
  | "json";

/** 自由文の置換に使う種別（実名 → 仮名の対応表に載せる） */
const LITERAL_KINDS: MaskKind[] = [
  "apName",
  "siteName",
  "floorName",
  "clientName",
  "ssid",
  "serial",
  "profileName",
  "rfTemplateName",
  "envName",
  "tag",
];

const STORAGE_ENABLED = "demoMask.enabled";
const STORAGE_REGISTRY = "demoMask.registry.v1";

// ---------------------------------------------------------------------------
// トグルの状態
// ---------------------------------------------------------------------------

let enabledCache: boolean | null = null;

function readStorage(key: string): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(key, value);
  } catch {
    /* プライベートモード等で書けなくても動作は続ける */
  }
}

/** マスクが有効か。**OFF のときは変換関数を通さない**（性能とデバッグのしやすさのため） */
export function isMaskEnabled(): boolean {
  if (enabledCache === null) enabledCache = readStorage(STORAGE_ENABLED) === "on";
  return enabledCache;
}

/** 状態を保存する。呼び出し側は反映のためにリロードする（取得済みのデータを作り直すため） */
export function setMaskEnabled(next: boolean): void {
  enabledCache = next;
  writeStorage(STORAGE_ENABLED, next ? "on" : "off");
}

// ---------------------------------------------------------------------------
// 採番（実名 → 連番）
// ---------------------------------------------------------------------------

export interface Registry {
  /** 種別 → （正規化した実名 → 連番） */
  slots: Map<MaskKind, Map<string, number>>;
  /** 種別 → 次に配る番号 */
  next: Map<MaskKind, number>;
  /** 自由文の置換に使う 実名 → 仮名。長い順に適用する */
  literals: Map<string, string>;
  /** literals を長い順に並べたキャッシュ（変更で無効化する） */
  sorted: [string, string][] | null;
  dirty: boolean;
}

export function createRegistry(): Registry {
  return { slots: new Map(), next: new Map(), literals: new Map(), sorted: null, dirty: false };
}

let registry: Registry = createRegistry();
let registryLoaded = false;

/** 収集フェーズ中か。true の間は採番せず、新出の値を `pending` に溜める */
let collecting = false;
let pending: Map<MaskKind, Set<string>> = new Map();

/** テスト用。独立したレジストリで純関数として検証できるようにする */
export function useRegistry(next: Registry): void {
  registry = next;
  registryLoaded = true;
}

function ensureRegistry(): Registry {
  if (registryLoaded) return registry;
  registryLoaded = true;
  const raw = readStorage(STORAGE_REGISTRY);
  if (!raw) return registry;
  try {
    const data = JSON.parse(raw) as Record<string, Record<string, number>>;
    for (const [name, values] of Object.entries(data)) {
      const kind = name as MaskKind;
      const map = new Map<string, number>();
      let max = 0;
      for (const [value, idx] of Object.entries(values)) {
        map.set(value, idx);
        if (idx > max) max = idx;
      }
      registry.slots.set(kind, map);
      registry.next.set(kind, max + 1);
      if (LITERAL_KINDS.indexOf(kind) >= 0) {
        map.forEach((idx, value) => rememberLiteral(value, formatSimple(kind, idx)));
      }
    }
  } catch {
    /* 壊れていたら捨てて採番し直す */
  }
  return registry;
}

/** 変更があればまとめて保存する */
export function persistRegistry(): void {
  const reg = ensureRegistry();
  if (!reg.dirty) return;
  const out: Record<string, Record<string, number>> = {};
  reg.slots.forEach((map, kind) => {
    const values: Record<string, number> = {};
    map.forEach((idx, value) => { values[value] = idx; });
    out[kind] = values;
  });
  writeStorage(STORAGE_REGISTRY, JSON.stringify(out));
  reg.dirty = false;
}

/** FNV-1a（32bit）。同じ文字列なら常に同じ値。採番の**順序**を決めるのに使う。 */
function hash32(input: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

function slotMap(kind: MaskKind): Map<string, number> {
  const reg = ensureRegistry();
  let map = reg.slots.get(kind);
  if (!map) {
    map = new Map();
    reg.slots.set(kind, map);
  }
  return map;
}

function assign(kind: MaskKind, key: string): number {
  const reg = ensureRegistry();
  const map = slotMap(kind);
  const hit = map.get(key);
  if (hit !== undefined) return hit;
  const idx = reg.next.get(kind) ?? 1;
  reg.next.set(kind, idx + 1);
  map.set(key, idx);
  reg.dirty = true;
  return idx;
}

/**
 * 連番を引く。収集フェーズ中は採番せず 0 を返す（1 周目の出力は捨てられる）。
 * 収集フェーズの外（自由文の走査など）で新しく出てきた値はその場で採番する。
 */
function slotFor(kind: MaskKind, key: string): number {
  const hit = slotMap(kind).get(key);
  if (hit !== undefined) return hit;
  if (collecting) {
    let set = pending.get(kind);
    if (!set) {
      set = new Set();
      pending.set(kind, set);
    }
    set.add(key);
    return 0;
  }
  return assign(kind, key);
}

/** 収集した新出の値を、実名のハッシュ順に並べてから連番を配る */
function flushPending(): void {
  pending.forEach((keys, kind) => {
    const ordered: string[] = [];
    keys.forEach((key) => ordered.push(key));
    ordered.sort((a, b) => {
      const ha = hash32(kind + ":" + a);
      const hb = hash32(kind + ":" + b);
      return ha === hb ? (a < b ? -1 : a > b ? 1 : 0) : ha - hb;
    });
    for (const key of ordered) assign(kind, key);
  });
  pending = new Map();
}

function rememberLiteral(real: string, masked: string): void {
  // 短すぎる語は別の文字列の一部を壊しうるので自由文の置換には使わない
  if (real.length < 3) return;
  if (registry.literals.get(real) === masked) return;
  registry.literals.set(real, masked);
  registry.sorted = null;
}

function literalsSorted(): [string, string][] {
  const reg = ensureRegistry();
  if (reg.sorted === null) {
    const entries: [string, string][] = [];
    reg.literals.forEach((masked, real) => entries.push([real, masked]));
    entries.sort((a, b) => b[0].length - a[0].length);
    reg.sorted = entries;
  }
  return reg.sorted;
}

// ---------------------------------------------------------------------------
// 仮名の書式
// ---------------------------------------------------------------------------

function pad(n: number, width: number): string {
  return String(n).padStart(width, "0");
}

/** 1 → A, 26 → Z, 27 → AA（サイト名用） */
function letters(n: number): string {
  let out = "";
  let v = n;
  while (v > 0) {
    const r = (v - 1) % 26;
    out = String.fromCharCode(65 + r) + out;
    v = Math.floor((v - 1) / 26);
  }
  return out || "A";
}

/** 元の値に依存しない書式（自由文の対応表を作り直すときにも使う） */
function formatSimple(kind: MaskKind, idx: number): string {
  switch (kind) {
    case "apName":
      return `AP-${pad(idx, 3)}`;
    case "siteName":
      return `Site ${letters(idx)}`;
    case "floorName":
      return `Floor ${idx}`;
    case "clientName":
      return `Client-${pad(idx, 3)}`;
    case "ssid":
      return `SSID-${pad(idx, 3)}`;
    case "profileName":
      return `Profile-${pad(idx, 3)}`;
    case "rfTemplateName":
      return `RF-Template-${pad(idx, 3)}`;
    case "envName":
      return `Env-${pad(idx, 3)}`;
    case "tag":
      return `Tag-${pad(idx, 3)}`;
    case "serial":
      return `DEMO${pad(idx, 8)}`;
    default:
      return `X-${pad(idx, 3)}`;
  }
}

function maskNamed(kind: MaskKind, raw: string): string {
  const key = raw.trim();
  const idx = slotFor(kind, key);
  if (idx === 0) return raw; // 収集フェーズ。この出力は捨てられる
  const masked = formatSimple(kind, idx);
  rememberLiteral(key, masked);
  return masked;
}

// -- MAC ---------------------------------------------------------------------

const MAC_SEP = /[:.-]/g;
const MAC_HEX = /^[0-9a-fA-F]{12}$/;
/** 区切りのある MAC（自由文の中を探すとき用。区切り無しの 12 桁は UUID の一部と紛れる） */
const MAC_IN_TEXT = /\b[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}\b/g;

/** 画面側の MAC はローカル管理アドレス `02:f…`（CSV 仮名化の `020…` `021…` と衝突しない） */
function isMaskedMac(value: string): boolean {
  return value.replace(MAC_SEP, "").toLowerCase().startsWith("02f");
}

function maskMac(raw: string): string {
  const trimmed = raw.trim();
  const hex = trimmed.replace(MAC_SEP, "");
  if (!MAC_HEX.test(hex)) return maskNamed("apName", trimmed); // MAC の形でなければ名前として扱う
  if (isMaskedMac(trimmed)) return trimmed;
  const idx = slotFor("mac", hex.toLowerCase());
  if (idx === 0) return raw;
  const flat = "02f" + idx.toString(16).padStart(9, "0");
  const upper = /[A-F]/.test(trimmed) && !/[a-f]/.test(trimmed);
  const body = upper ? flat.toUpperCase() : flat;
  // 桁数・区切りは入力に合わせる（崩れるとレイアウトが変わり、加工が露骨に分かる）
  const sep = trimmed.includes(":") ? ":" : trimmed.includes("-") ? "-" : trimmed.includes(".") ? "." : "";
  if (sep === "") return body;
  const size = sep === "." ? 4 : 2;
  const parts: string[] = [];
  for (let i = 0; i < body.length; i += size) parts.push(body.slice(i, i + size));
  return parts.join(sep);
}

// -- IP ----------------------------------------------------------------------

const IPV4 = /^\d{1,3}(?:\.\d{1,3}){3}$/;
const IPV4_IN_TEXT = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
/** RFC 5737（ドキュメント用）。使い切ったら RFC 2544 のベンチマーク用レンジへ延ばす */
const DOC_BLOCKS = ["192.0.2", "198.51.100", "203.0.113"];

function isDocIp(value: string): boolean {
  return DOC_BLOCKS.some((b) => value.startsWith(b + ".")) || value.startsWith("198.18.") || value.startsWith("198.19.");
}

function docIp(idx: number): string {
  if (idx <= DOC_BLOCKS.length * 254) {
    const block = Math.floor((idx - 1) / 254);
    const host = ((idx - 1) % 254) + 1;
    return `${DOC_BLOCKS[block]}.${host}`;
  }
  const i = idx - DOC_BLOCKS.length * 254 - 1;
  return `198.18.${(i >> 8) & 0xff}.${i & 0xff}`;
}

function maskIp(raw: string): string {
  const trimmed = raw.trim();
  if (!IPV4.test(trimmed)) return maskNamed("clientName", trimmed); // IPv6 等は名前として扱う
  if (isDocIp(trimmed)) return trimmed;
  const idx = slotFor("ip", trimmed);
  return idx === 0 ? raw : docIp(idx);
}

// -- シリアル ----------------------------------------------------------------

function maskSerial(raw: string): string {
  const trimmed = raw.trim();
  const idx = slotFor("serial", trimmed.toUpperCase());
  if (idx === 0) return raw;
  const len = trimmed.length;
  const prefix = len >= 8 ? "DEMO" : "D";
  const masked = prefix + pad(idx, Math.max(1, len - prefix.length));
  rememberLiteral(trimmed, masked);
  return masked;
}

// -- サイト名・サイトラベル --------------------------------------------------

/** 固定の架空住所。住所は番号を振っても意味が無いので 1 つに寄せる */
export const DEMO_ADDRESS = "1-1-1 Demo, Tokyo, Japan";

/** ローダが作る `サイト名 [site_id]` の形。ID は URL と同じ扱いで残す（PRECONDITION 4） */
const SITE_LABEL = /^(.*?)\s*\[([^\]]*)\]$/;
/** 対象サイトの指定が無いときの表記（backend/hangap/analysis.py ALL_SITES_TEXT ほか） */
const KEEP_AS_IS = ["すべて", "(指定なし)", "-", ""];

function maskSiteLabel(raw: string): string {
  const trimmed = raw.trim();
  if (KEEP_AS_IS.indexOf(trimmed) >= 0) return raw;
  const m = SITE_LABEL.exec(trimmed);
  if (m) {
    const name = m[1].trim();
    const masked = name === "" ? "" : maskNamed("siteName", name);
    return `${masked} [${m[2]}]`;
  }
  return maskNamed("siteName", trimmed);
}

/** `A [id], B [id2]` のように並んだ対象サイトの表記 */
function maskSiteListText(raw: string): string {
  return raw
    .split(",")
    .map((part) => (part.startsWith(" ") ? " " : "") + maskSiteLabel(part))
    .join(",");
}

// ---------------------------------------------------------------------------
// 自由文
// ---------------------------------------------------------------------------

/** `分析条件: 対象サイト=… / 窓 …` の対象サイト部分（レジストリに無くても必ず落とす） */
const COND_SITES = /(対象サイト=)(.*?)(?= \/ |$)/g;

/**
 * 自由文の中の識別情報を落とす。
 * 1. 構造の分かっている箇所（`対象サイト=`）を先に置き換える
 * 2. これまでに採番した実名を長い順に置き換える
 * 3. MAC / IPv4 は正規表現で拾う
 *
 * **完全ではない。** 一度も採番していない実名が地の文に埋まっていれば残る。
 */
export function scrubText(raw: string): string {
  if (!raw) return raw;
  let out = raw.replace(COND_SITES, (_m, head: string, body: string) => head + maskSiteListText(body));
  for (const [real, masked] of literalsSorted()) {
    if (out.indexOf(real) >= 0) out = out.split(real).join(masked);
  }
  out = out.replace(MAC_IN_TEXT, (m) => (isMaskedMac(m) ? m : maskMac(m)));
  out = out.replace(IPV4_IN_TEXT, (m) => (isDocIp(m) ? m : maskIp(m)));
  return out;
}

// ---------------------------------------------------------------------------
// フィールド名 → 種別
// ---------------------------------------------------------------------------

const KEY_KINDS: Record<string, MaskKind> = {
  // AP
  ap_name: "apName",
  neighbor_name: "apName",
  from_ap_name: "apName",
  to_ap_name: "apName",
  "周辺AP名": "apNameList",
  ap_list: "apNameList",
  // サイト
  site_name: "siteName",
  requested_site: "siteName",
  site_label: "siteLabel",
  address: "address",
  // フロア
  map_name: "floorName",
  default_floor: "floorName",
  // クライアント
  hostname: "clientName",
  client_name: "clientName",
  // ネットワーク識別子
  mac: "mac",
  ap_mac: "mac",
  bssid: "mac",
  neighbor_mac: "mac",
  client_mac: "mac",
  ip: "ip",
  ip_address: "ip",
  ssid: "ssid",
  serial: "serial",
  serial_number: "serial",
  // Mist の設定オブジェクト名（顧客名が入りうる）
  deviceprofile_name: "profileName",
  rftemplate_name: "rfTemplateName",
  // 資格情報（トークンは先頭 10 文字がサーバから来る。画面共有では隠す）
  mist_api_token: "token",
  // 利用者が付けた自由入力
  tags: "tag",
  // JSON 文字列（中に ap_name 等が入る）
  metrics_json: "json",
  // 自由文
  condition_text: "freeText",
  result_summary_text: "freeText",
  report_text: "freeText",
  detail: "freeText",
  recommendation: "freeText",
  error: "freeText",
  message: "freeText",
  warnings: "freeText",
  floor_resolution_notes: "freeText",
  actions: "freeText",
};

/**
 * 子のキーが「フィールド名」ではなく「列名」である入れ物。
 * 例: `column_kinds` は `{"ap_name": "text"}` なので、キーで置き換えると値が壊れる。
 */
const OPAQUE_KEYS = new Set(["column_kinds"]);

/**
 * `name` は種別が名前から決まらないので、同じオブジェクトの他のキーで判断する。
 * **null を返した場合は置き換えない**（保存済み結果の `name` はダウンロード・削除の
 * キーなので、置き換えると機能が壊れる）。
 */
function nameKind(obj: Record<string, unknown>): MaskKind | null {
  if ("saved_at" in obj || "total_bytes" in obj || "files" in obj) return null; // 保存済み結果
  if ("job_id" in obj || "columns" in obj || "rows" in obj) return null; // 結果テーブル
  if ("mac" in obj || "model" in obj || "radio_24" in obj) return "apName";
  if ("width" in obj || "height" in obj || "ppm" in obj) return "floorName";
  if ("mist_org_id" in obj || "mist_base_url" in obj) return "envName";
  // 残る `{id, name}` はすべてサイト（SiteInfo / SiteSimple / SnapshotSite / SiteSummary）
  return "siteName";
}

function targetNameKind(obj: Record<string, unknown>): MaskKind {
  const type = obj["target_type"];
  if (type === "ap") return "apName";
  if (type === "ap_pair") return "apPairName";
  return "clientName"; // client（ホスト名。ホスト名が無い端末では MAC が入る）
}

export function resolveKind(key: string, obj: Record<string, unknown>): MaskKind | null {
  if (key === "name") return nameKind(obj);
  if (key === "target_name") return targetNameKind(obj);
  return KEY_KINDS[key] ?? null;
}

// ---------------------------------------------------------------------------
// 値の置き換え
// ---------------------------------------------------------------------------

/** ホスト名・端末名の欄に MAC が入っていることがある（hostname が無い端末） */
function looksLikeMac(value: string): boolean {
  return MAC_HEX.test(value.replace(MAC_SEP, ""));
}

export function maskScalar(kind: MaskKind, raw: string): string {
  if (raw.trim() === "") return raw;
  switch (kind) {
    case "apName":
      return looksLikeMac(raw) ? maskMac(raw) : maskNamed("apName", raw);
    case "clientName":
      return looksLikeMac(raw) ? maskMac(raw) : maskNamed("clientName", raw);
    case "siteName":
      return maskNamed("siteName", raw);
    case "siteLabel":
      return maskSiteLabel(raw);
    case "floorName":
      return maskNamed("floorName", raw);
    case "ssid":
      return maskNamed("ssid", raw);
    case "profileName":
      return maskNamed("profileName", raw);
    case "rfTemplateName":
      return maskNamed("rfTemplateName", raw);
    case "envName":
      return maskNamed("envName", raw);
    case "tag":
      return maskNamed("tag", raw);
    case "address":
      return DEMO_ADDRESS;
    case "mac":
      return maskMac(raw);
    case "ip":
      return maskIp(raw);
    case "serial":
      return maskSerial(raw);
    case "token":
      return "*".repeat(raw.length);
    case "apNameList":
      // `A, B, C` の並び。区切りと件数は保つ
      return raw
        .split(",")
        .map((part) => {
          const body = part.trim();
          return body === "" ? part : (part.startsWith(" ") ? " " : "") + maskNamed("apName", body);
        })
        .join(",");
    case "apPairName":
      // `AP1 ↔ AP2`
      return raw
        .split("↔")
        .map((part) => {
          const body = part.trim();
          return body === "" ? part : maskNamed("apName", body);
        })
        .join(" ↔ ");
    case "json":
      return maskJsonString(raw);
    case "freeText":
      return scrubText(raw);
    default:
      return raw;
  }
}

function maskJsonString(raw: string): string {
  try {
    return JSON.stringify(walk(JSON.parse(raw)));
  } catch {
    return scrubText(raw);
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function transformValue(value: unknown, kind: MaskKind): unknown {
  if (Array.isArray(value)) return value.map((v) => transformValue(v, kind));
  if (isPlainObject(value)) return walk(value); // 想定外の形。取りこぼさないよう再帰する
  if (typeof value !== "string") return value; // 数値・真偽値・null は対象外
  return maskScalar(kind, value);
}

/** オブジェクト・配列を再帰的にたどり、フィールド名がルールに一致する値だけを置き換える */
function walk(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(walk);
  if (!isPlainObject(value)) return value;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(value)) {
    if (OPAQUE_KEYS.has(key)) {
      out[key] = value[key];
      continue;
    }
    const kind = resolveKind(key, value);
    out[key] = kind === null ? walk(value[key]) : transformValue(value[key], kind);
  }
  return out;
}

/**
 * API 応答を置き換える。**2 周する。**
 * 1 周目は採番対象を集めるだけ（出力は捨てる）。集め終わってからハッシュ順に連番を
 * 配り、2 周目で実際に置き換える。こうすると
 * - 配列の並び順が変わっても番号が変わらない
 * - `condition_text` のような自由文の置換で、同じ応答に入っている実名を使える
 */
export function maskPayload<T>(data: T): T {
  collecting = true;
  try {
    walk(data);
  } finally {
    collecting = false;
  }
  flushPending();
  const out = walk(data) as T;
  persistRegistry();
  return out;
}

/** トグルが ON のときだけ置き換える */
export function maskResponse<T>(data: T): T {
  if (!isMaskEnabled()) return data;
  return maskPayload(data);
}

/** エラーメッセージなど、単体の文字列を落とす */
export function maskMessage(text: string): string {
  if (!isMaskEnabled()) return text;
  const out = scrubText(text);
  persistRegistry();
  return out;
}

// ---------------------------------------------------------------------------
// ダウンロード・Floor Map の一律無効化（29番）
// ---------------------------------------------------------------------------
//
// マスク ON 中、ダウンロードの一部は実名で落ち、一部は仮名の値をそのままクエリ・
// パスに渡すため壊れる（空の CSV、フロア不一致など）。壊れるものだけを止めると
// 「押せるものと押せないものが混在」して利用者が覚えていなければならなくなるので、
// **一律で無効化する。** Floor Map は背景画像に部屋名・棟名が焼き込まれており
// `lib/api.ts` の変換が原理的に効かないため、同様に一律で無効化する。

/** マスク ON 中はすべてのダウンロード導線を無効化する */
export function downloadsDisabled(): boolean {
  return isMaskEnabled();
}

export const DOWNLOAD_DISABLED_TITLE = "マスク中はダウンロードできません";

/** マスク ON 中は Floor Map の内容を表示しない */
export function floorMapBlocked(): boolean {
  return isMaskEnabled();
}

export const FLOOR_MAP_BLOCKED_TITLE = "マスク中は表示できません（背景画像は匿名化できません）";

/**
 * マスク ON のときだけ、自由文（警告等）の置換に備えて実名（AP 名等）を先行取得する。
 * OFF のときは何もしない（余計なリクエストを増やさない）。失敗しても画面の機能は
 * 止めない（コンソールに記録するだけ）。
 */
export async function prefetchForMask(fetcher: () => Promise<unknown>): Promise<void> {
  if (!isMaskEnabled()) return;
  try {
    await fetcher();
  } catch (e) {
    console.error("[mask] prefetch failed", e);
  }
}

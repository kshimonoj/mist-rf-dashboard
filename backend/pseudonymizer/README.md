# pseudonymizer — Mist Dashboard ログの仮名化 CLI

Mist Dashboard が `data/logs/` に出力する CSV ログから、顧客・個人を特定しうる情報を
**一貫性のある仮名に置換**する CLI。

これは**マスキング（不可逆な情報の破棄）ではなく仮名化（pseudonymization）**である。
同じ値は常に同じ別名に置き換わるため、AP 単位・クライアント単位・時系列の分析可能性は保たれる。

---

## 正直な限界

**この機能は「安全になる」ものではなく「再識別リスクを下げる」ものである。**

> 列の仮名化は「誰の AP か」を隠すが、「どこの何の話か」までは隠さない。
> 例えば「AP 250 台・ピーク 7,400 接続・45 分で 89% が退場」というログは、
> 列を1つも見なくても大規模会場の特定イベントを示唆する。
> AP 台数・接続端末数の規模は分析に必要なため除去できず、**残留リスクとして受け入れる**。
> タイムシフトにより日付という最も強い手がかりは除去されるが、
> この機能は「安全になる」ものではなく「再識別リスクを下げる」ものである。

具体的に残るもの:

- **規模**（AP 台数、クライアント数、フロア数、SSID 数）
- **形状**（時間帯別の接続数カーブ、チャネル設計、AP モデル、ベンダー分布）
- **相対時刻**（タイムシフトは全ファイル一律のオフセット加算なので、時刻差はそのまま残る）

タイムシフト量は既定で**日単位**で選ぶため、時刻（time-of-day）は保存され、日付（曜日を含む）が
失われる。これは「21:00 の一斉退場」のような時間帯依存の分析を壊さないための意図的な設計である。

`--shift-granularity week` を指定すると**週単位**のシフトになり、曜日と時刻の両方が保存される
（曜日パターン分析に使える）。ただし「土曜夕方の混雑」のように会場イベントの絞り込みに効く
情報が残るため、既定より再識別リスクが上がる。用途が無い限り既定（day）を使うこと。

仮名化済みログを外部に共有する前に、上記の残留リスクを踏まえて判断すること。

---

## 使い方

```
python -m pseudonymizer <入力パス...> --out <出力ディレクトリ> [options]

  <入力パス...>       ファイル、ディレクトリ、または glob パターン（複数可）
  --out DIR           出力先ディレクトリ（必須）
  --salt-file PATH    ソルトファイル（既定: <out>/.pseudonym_salt.json）
  --unknown-column {error,drop,keep}   既定: error
  --keep-vlan         vlan_id を変換せず保持する
  --no-time-shift     タイムシフトを行わない（非推奨。警告を出す）
  --shift-granularity {day,week}   新規ソルト生成時のタイムシフト粒度（既定: day）
  --dry-run           出力せず、検出した種別・列・変換件数のみ表示
```

例:

```bash
cd backend
# まず中身を確認する
python -m pseudonymizer /app/data/logs --out ~/pseudo-logs --dry-run

# 実行（ソルトが無ければ生成される）
python -m pseudonymizer /app/data/logs --out ~/pseudo-logs

# 後から届いたログを、同じソルトで追加処理する
python -m pseudonymizer new_logs/ --out ~/pseudo-logs-2 \
    --salt-file ~/pseudo-logs/.pseudonym_salt.json
```

出力ファイル名は入力と同じで、ディレクトリだけが変わる。
**出力先が入力と同じディレクトリの場合はエラー停止する**（入力を上書きしないため）。

1 ファイルでも leak check（後述）に引っかかった場合、**そのバッチは 1 ファイルも書き出さない**。

---

## 対象ファイル種別

**CSV ヘッダー行の列集合**で種別を判定する。ファイル名は判定に一切使わない
（列順は無視するが、列の過不足は不一致として扱う）。既知のどの種別の列集合とも
完全一致しない場合はエラー停止する（`filename_20240101...csv` のような命名規則は
実データでは網羅しきれないため、ファイル名パターンによる判定は廃止した）。

この方式により、`ap_events_backfill_*.csv`（`ap_events` と同一ヘッダー）や
`floormap_*_manual_summary.csv`（`floormap_summary` と同一ヘッダー）のような
命名バリエーションは、**追加定義なしで既存種別に吸収される**。

| 種別キー | 列数 | 備考 |
|---|---|---|
| `ap_metrics` | 33 | |
| `ap_events` | 11 | `ap_events_backfill_*.csv` を含む |
| `client_metrics` | 36 | |
| `sle_metrics` | 28 | |
| `floormap_summary` | 8 | `floormap_*_manual_summary.csv` を含む |
| `floormap_ap_detail` | 24 | フロア図上の AP ごとの生データ（座標・チャネル・電力等） |

変換ルールは**グローバルな辞書 1 つ**（`schemas.COLUMN_RULES`）だけで定義し、
種別ごとには「通す列のホワイトリスト」（`FileType.columns`）だけを持つ。
`mac` のように種別で意味が変わる列だけ `FileType.overrides` で解決する
（`ap_metrics` / `floormap_ap_detail` の `mac` は AP の MAC、`client_metrics` の `mac` は端末の MAC）。

新しいファイル種別を追加するときは `schemas.py` に `FileType` を 1 つ足すだけでよい。
ただし既存のどの種別とも列集合が重複しないこと（重複すると import 時に自己チェックで
`RuntimeError` になる）。

### `floormap_ap_detail` の列

```
timestamp, site_id, site_name, map_id, map_name, ap_name, mac, model, status,
band_24_channel, band_24_bandwidth, band_24_power, band_24_noise_floor,
band_5_channel,  band_5_bandwidth,  band_5_power,  band_5_noise_floor,
band_6_channel,  band_6_bandwidth,  band_6_power,  band_6_noise_floor,
num_clients, x_m, y_m
```

`x_m` / `y_m` はフロア図原点からの相対座標で、単体では場所を特定できないうえ、
変換すると後続の距離計算が壊れるため PASSTHROUGH（変換しない）。

### `--unknown-column` との関係

ヘッダーが既知の種別と完全一致しない場合でも、**既知の種別のホワイトリストが
ヘッダーの部分集合になっている**（＝既知の列 + 未知の追加列）ケースに限り、
`--unknown-column` の drop/keep モードが従来どおり機能する。候補が 0 個または
複数見つかった場合（本当にどの種別か判別できない場合）は、モードに関わらず
エラー停止する。

### 変換型と出力形式

| 変換型 | 対象列 | 出力形式 |
|---|---|---|
| `SITE_ID` | `site_id` | `20000000-0000-4000-8000-{連番12桁}` |
| `SITE_NAME` | `site_name` | `SITE_{連番3桁}` |
| `AP_ID` | `ap_id` | `10000000-0000-4000-8000-{連番12桁}` |
| `AP_NAME` | `ap_name` | `AP_{連番4桁}` |
| `AP_MAC` | `mac`(ap_metrics, floormap_ap_detail), `ap_mac`, `bssid` | `020` + 連番 9 桁 hex |
| `CLIENT_MAC` | `mac`(client_metrics) | `021` + 連番 9 桁 hex（AP_MAC と別系列） |
| `HOSTNAME` | `hostname` | `HOST_{連番4桁}` |
| `IP` | `ip` | `10.{連番}.{連番}.{連番}` |
| `SSID` | `ssid` | `SSID_{連番3桁}` |
| `MAP_NAME` | `map_name` | `FLOOR_{連番3桁}` |
| `MAP_ID` | `map_id` | `30000000-0000-4000-8000-{連番12桁}` |
| `AP_NAME_LIST` | `ap_list` | カンマ分割 → 各要素に `AP_NAME` 適用 → 再結合 |
| `VLAN` | `vlan_id` | `{連番}`（`--keep-vlan` で保持） |
| `TIMESTAMP` | `timestamp`, `event_timestamp` | 全ファイル一律のオフセット加算 |
| `PASSTHROUGH` | 上記以外のホワイトリスト列 | 変更しない |

補足:

- MAC は **全バイトを置換**する（OUI を残さない）。ベンダー情報は `manufacture` 列に残るため、
  分析価値は失われない。MAC の入力はコロン有無・大文字小文字を問わず正規化して照合する。
- `hostname` / `ssid` / `map_name` は自由記述であり、**「安全な形式か」を判定せず無条件で置換**する。
  `map_name` にはビル名がそのまま入るため、顧客名より直接的に場所を特定する。
- `AP_NAME` / `AP_MAC` / `AP_ID` は同じ番号空間を共有する。同一行に現れた値を同じ AP とみなして
  リンクするため、`AP_0200` ↔ `0200000000c8` ↔ `10000000-0000-4000-8000-000000000200` のように
  対応が取れる。**対応が取れない場合でも処理は止まらず、それぞれ独立に採番される。**
  `bssid` は無線／SSID ごとの識別子なので AP 本体とはリンクせず、独立に採番する。
- `MAP_ID`（`map_id`）は `SITE_ID` / `AP_ID` とは**独立した名前空間**で採番する。
  `map_id` と `map_name` は同一のフロアを指すが、両者の番号対応を取る仕組みは無い
  （`AP_ID`/`AP_NAME`/`AP_MAC` のような Union-Find 連結は行わない）。

---

## ソルトとマッピングの管理

既定のパス（`--salt-file` で変更可）:

```
<出力先ディレクトリ>/.pseudonym_salt.json    # ソルトとタイムオフセット
<ソルトと同じディレクトリ>/.pseudonym_map.json  # 元の値 → 連番の割り当て
```

`.pseudonym_salt.json` の内容:

```json
{
  "version": 1,
  "salt": "<32バイトのhex>",
  "time_offset_seconds": -87091200,
  "created_at": "2026-08-14T12:00:00+00:00",
  "shift_granularity": "day"
}
```

- ファイルが無ければ生成し、あれば読み込んで再利用する。生成時は stderr に警告を出す。
- `shift_granularity` が記録されていない旧形式のソルトファイルは `week` として扱い、
  警告を出す（既に仮名化済みのログとの一貫性を壊さないため。オフセット自体は
  ファイルに記録された値をそのまま使うので、動作は変わらない）。
- **両ファイルとも 0600 で作成される。ファイル自体が機密である。**
- **これらを失うと、過去に仮名化したログとの対応が切れる。**
  復旧手段は無い。バックアップはリポジトリ外の安全な場所に取ること。
- `.pseudonym_map.json` は別のソルトで作られたものを読み込むとエラー停止する
  （ソルトの指紋を保存しているため）。
- `--dry-run` はソルトファイルを作らない。既存のソルトが無ければその実行限りの
  一時ソルトを使い、マッピングも保存しない。

### 採番の決定論性について

- 新しい値には `HMAC-SHA256(salt, "<変換型>:<値>")` の昇順で 1 から連番を割り当てる。
- 割り当て結果は `.pseudonym_map.json` に永続化され、以降の実行で再利用される。
  これにより「いつ・何回・どのファイルに対して実行しても、同一の入力値は同一の仮名になる」。
- マッピングファイルが無い状態でも、**同じソルト・同じ入力集合**なら結果は完全に同じになる
  （テスト `test_same_salt_without_mapping_cache_is_byte_identical` で担保）。
  入力集合が変わると新規値の連番の並びは変わりうるため、**マッピングファイルこそが
  実行をまたいだ一貫性の担保**である。ソルトと同じ重みで保全すること。

---

## leak check（出力前の再スキャン）

出力を書き出す前に、生成した内容そのものを再スキャンする。
1 件でも検出したら**出力を破棄してエラー終了**する。

| 規則名 | 内容 |
|---|---|
| `uuid_not_pseudonymized` | UUID 形式（生成した仮名 UUID のパターンは除外。`AP_ID`=1、`SITE_ID`=2、`MAP_ID`=3 始まり） |
| `mac_not_pseudonymized` | `02` 以外で始まる MAC 形式（実在 OUI の残存） |
| `private_ip_not_pseudonymized` | `192.168.` / `10.` / `172.16`〜`172.31.`（生成した仮名 IP は除外） |
| `column_not_in_whitelist` | ホワイトリストに存在しない列名 |
| `non_ascii_character` | 非 ASCII 文字（日本語の施設名・SSID・ホスト名の残存） |

- **エラーメッセージには検出した値そのものを出力しない。** 列名・行番号・規則名のみを出す
  （漏れた値をターミナルに出したら意味がないため）。
- `10.x.x.x` は仮名 IP と形式が同じなので、**生成済みの仮名 IP 集合に含まれるかどうか**で判定する。
- 12 桁の 10 進数（`tx_bytes` 等）を MAC と誤検出しないよう、MAC 列以外では
  数字のみのトークンは MAC とみなさない。MAC 列（`mac` / `ap_mac` / `bssid`）は厳密に判定する。
- `--unknown-column keep` で明示的に通すと決めた列は `column_not_in_whitelist` の対象外になる
  （代わりに実行時に大きな警告を出す）。他の 4 規則はそのまま適用される。

---

## 未知の列への対応

Mist 側の仕様変更で列が増えた場合に、**既定で素通りしない**設計にしてある。

| モード | 挙動 |
|---|---|
| `error`（既定） | エラー停止。列名を表示して終了 |
| `drop` | その列を出力から除外し、警告を stderr に出す |
| `keep` | そのまま通す。**危険なため、明示的な警告を出す** |

ホワイトリストにあるのに入力に存在しない列がある場合も、警告を出したうえで処理を続ける。

---

## テスト

```bash
pytest backend/tests/pseudonymizer/
```

**フィクスチャは全て合成データである。**実データ・実データ由来の値は含まれない
（MAC は `aabbccddee01`、UUID は `00000000-0000-4000-8000-...` のように、
実データと誤認しようがない値を使っている）。

---

## 構成

```
backend/pseudonymizer/
  schemas.py     # 種別定義・ホワイトリスト・変換型辞書
  transforms.py  # 変換型ごとの実装と採番エンジン
  salt.py        # ソルト生成・読込・タイムオフセット
  leakcheck.py   # 出力検証
  cli.py         # CLI エントリポイント
```

この段階では CLI のみで、API エンドポイント化・UI 追加は行っていない。

# backfill — 正本（DB）から派生物（CSV ログ）を再生成する

`data/logs` の CSV は **派生物** であり、正本は DB（SQLite）である。
ディスク障害・ローテートの事故・収集停止・CSV 仕様変更などで派生物が失われたとき、
このディレクトリのコマンドで正本から作り直す。

アプリ本体（`routers` / `scheduler` / `hangap`）とは分離した、
運用者が手で叩くコマンドの置き場所。ネットワークアクセス（Mist API 呼び出し）は行わない。

## ap_metrics — 1 時間ごとの AP メトリクス CSV

```
python -m backfill.ap_metrics [--from TIME] [--to TIME] [--db PATH] [--logs-dir DIR] [--write]
```

Docker 環境ではコンテナ内で実行する:

```bash
# dry-run（既定。1 ファイルも書き出さない）
docker compose exec -T backend python -m backfill.ap_metrics \
  --from "2026-08-09 16:00" --to "2026-08-16 00:00"

# 内容を確認してから、実際に書き出す
docker compose exec -T backend python -m backfill.ap_metrics \
  --from "2026-08-09 16:00" --to "2026-08-16 00:00" --write
```

### オプション

| オプション | 既定 | 説明 |
| --- | --- | --- |
| `--from` / `--to` | DB の全範囲 | **現地時刻**で指定する。`--to` は含まない（`[from, to)`） |
| `--db PATH` | 稼働中の DB | 読み込む SQLite ファイル。バックアップからの復元に使う |
| `--logs-dir DIR` | `data/logs` | 出力先 |
| `--snapshot-db PATH` | 稼働中の DB | `snapshots` の登録先。コンテナ外から実行するとき用 |
| `--write` | なし（dry-run） | **付けたときだけ**実際に書き出す |

`--write` を付けない限り、生成予定のファイル・行数・スキップ件数を表示するだけで、
ファイルにも `snapshots` テーブルにも一切触れない。まず dry-run で確認すること。

### 出力

- **1 時間ごとに 1 ファイル**。自動保存（`scheduler.save_hourly_logs`）と同じ粒度・同じ命名規則
  `ap_metrics_<YYYYMMDD>_<HHMM>_<TZ>.csv`。
  ファイル名の時刻は**対象期間の終端**（12:00〜13:00 のデータ → `..._1300_JST.csv`）。
- 列構成は `scheduler.ALL_CSV_COLUMNS`（現行 36 列）。
  座標列（`map_id` / `x_m` / `y_m`）を収集する前の行は空欄になる。それが正しい状態である。
- History 画面に出すため `snapshots` テーブルにも登録する。
  - `triggered_by` は **`restore`**（通常収集の `auto` / 手動保存の `manual` と区別できる）
  - `saved_at` は**実行時刻ではなく、そのログが対象とする期間の終端**。
    実行時刻を入れると History の末尾に全ファイルが固まって使い物にならない
  - `site_count` / `ap_count` は自動保存と同じ意味（サイト数 / レコード数）。
    `size_bytes` は保存せず、History 表示時にファイルサイズから求める（既存と同じ）

### 時刻の扱い（最重要）

**DB は UTC 保存・CSV は現地時刻（`app_settings.timezone`、既定 `Asia/Tokyo`）。**

- DB の `2026-08-09 03:00:39` は JST の `12:00:39`
- `--from` / `--to` も**現地時刻**として解釈する
- 変換を誤ると 9 時間ずれ、分析（hangap）の窓指定が全く合わなくなる

変換と列構成は `scheduler.ap_metrics_csv_row` / `scheduler.ALL_CSV_COLUMNS` を再利用しており、
このツール側で再実装していない（ローダは**ヘッダー完全一致**で種別を判定するため、
1 列でもずれると `ap_metrics` と認識されず読めなくなる）。

### 冪等性・既存ファイルの扱い

期間ごとに、ファイルの有無と `snapshots` への登録の有無で 3 通りに分岐する。
**既存ファイルの上書き・削除は一切しない。**

| ファイル | `snapshots` | 動作 | 表示 |
| --- | --- | --- | --- |
| あり | 登録済み | 何もしない | `=` |
| あり | 未登録 | ファイルは書き直さず、登録だけ行う | `~` |
| なし | — | 書き出して登録する | `+` |

2 番目は、前回の実行が登録前に落ちて残った「孤児ファイル」を再実行で救うための分岐。
このとき `site_count` / `ap_count` は DB の行数ではなく**ファイルの実物**から数える
（`snapshots` はファイルの説明だから）。

- 同じ範囲で 2 回実行してもファイルは重複せず、`snapshots` にも重複行はできない
- 書き出しは一時ファイル（`.csv.tmp`）経由の rename。途中で失敗しても、
  書きかけの `.csv` が「既存」と誤判定されることはない

### SQLite のロックについて（`database is locked` の再発防止）

読み込み元と `snapshots` の登録先が**同じ DB ファイル**になる構成（`--db` を省略した
通常のケース）では、読み取り接続がロックを掴んだままだと書き込みが弾かれる。
pysqlite は SELECT を遅延実行するため、`yield_per` で流し読みしながら書き込むと、
行数が多いときだけ `sqlite3.OperationalError: database is locked` になる
（行数が少ないと SELECT が読み切れてしまい再現しない）。

現在の実装はこれを構造的に避けている:

- 1 時間ぶんを `.all()` で**読み切ってから** `rollback()` し、読み取りトランザクションを閉じる
- `snapshots` への登録はループの中で行わず、**読み取り接続を閉じたあとに 1 トランザクション**でまとめる
  （148 ファイルなら 148 回ではなく 1 回の commit）
- SQLite 接続のロック待ちを 30 秒に設定する（既定の 5 秒は復旧作業には短い）

なお DB の `journal_mode` は SQLite 既定の `delete`（WAL ではない）。現在値は
`docker compose exec -T backend python -c "import sqlite3;print(sqlite3.connect('/app/data/mist.db').execute('PRAGMA journal_mode').fetchone())"`
で確認できる。上記の対策で読み書きが衝突しなくなったため、WAL への変更はしていない。

### サイト名の解決

`ap_metrics` テーブルは `site_name` を持たないため、CSV の `site_name` 列は
次の順で補う（Mist API は呼ばない）:

1. DB の `ap_events` テーブル
2. DB の `insights` テーブル
3. `data/logs` の既存 CSV（`site_id` と `site_name` を両方持つもの・新しい順）

解決できなかった `site_id` は空欄のまま出力し、件数を警告として表示する。

### 復元できないもの

**保持期間（`metrics_retention_days`）を過ぎて DB から削除された行は、DB にも無いので復元できない。**
`--db` で古いバックアップの SQLite ファイルを指定すれば、そこに残っている分は復元できる。
その場合も `snapshots` の登録先は稼働中の DB（History が読む DB）である。
登録先 DB を開けないときは、CSV を 1 件も書かずにその時点で止まる
（途中まで書いて終わると、次回実行時にその期間がスキップされてしまうため）。

長期間の復元が必要になりうる環境では、事前に `metrics_retention_days` を延ばし、
`long_history_enabled` を有効にしておくこと。

### 前例

`ap_events` にも同じ発想の backfill がある（`ap_events_backfill_*.csv` を出力する
`scheduler.backfill_ap_events`。こちらは Mist API から取り直す）。

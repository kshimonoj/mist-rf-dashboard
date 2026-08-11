# 02 - AP Eventsログ 過去1週間分ワンショット取得

## 推奨モデル: Sonnet

理由: 01と同じくAPI・DB・UIの仕様は確定済み。既存の`get_site_device_events()`・`ap_events`テーブル・
CSV保存ロジックを再利用する機械的な拡張タスクのため、調査や設計判断は発生しない。

---

Historyページに「過去7日分のAPイベントログを取得」ボタンを追加してください。
execute all commands automatically without stopping.

## 背景

01で実装した`ap_events`は1時間ごとのポーリング開始時点からしか蓄積されない
（`duration="1h"`で毎回直近1時間分のみ取得しているため）。導入前に発生していたイベント
（例: 過去1週間のAP_RESTARTED等）は取得できていない。

Mist APIの`devices/events/search`は`duration=7d`のような長期間もそのまま受け付けることを確認済み。
この仕組みを使い、ユーザーが任意のタイミングで手動実行できる「過去7日分の一括取得」ボタンを追加する。

## 【1】バックエンド: バックフィル関数

`scheduler.py`（または`mist/client.py`を呼び出す適切な場所）に新規関数を追加:

```python
async def backfill_ap_events(days: int = 7) -> dict:
    # 監視対象全サイトに対し get_site_device_events(site_id, duration=f"{days}d", limit=100) を実行
    # ページネーションで全件取得（totalがlimit超えの場合はpageを進める）
    # 取得したイベントを ap_events テーブルにINSERT OR IGNORE（既存の重複排除インデックスを使用）
    # 新規追加分のみを ap_events_backfill_YYYYMMDD_HHMM_JST.csv として data/logs/ に保存
    #   （通常の自動保存ファイルと見分けられるよう "backfill" を含むファイル名にする）
    # 戻り値: {"sites_processed": N, "new_events": N, "duplicate_events": N, "csv_file": "..."}
```

- 01の`save_ap_events_log()`とロジックを共通化できる部分（Mist呼び出し・INSERT OR IGNORE・CSV書き出し）は
  関数化して再利用してよい
- 実行時間が長くなる可能性があるため、サイトごとに順次処理し、途中でエラーが出ても他サイトの処理は継続する
  （エラーサイトはログに記録し、レスポンスの `errors: [{site_name, error}]` に含める）

## 【2】バックエンド: APIエンドポイント

```
POST /api/ap-events/backfill?days=7
  → backfill_ap_events(days) を実行し、結果を返す
  → days のデフォルトは7、範囲は1〜30（Mist側の実質的な検索可能期間の上限に合わせる）
  → 処理中に他のリクエストをブロックしないよう非同期で実行してよいが、
    今回はシンプルに「実行完了まで待って結果を返す」同期的な実装でよい
```

## 【3】フロントエンド: Historyページにボタン追加

`history/page.tsx` のCSV Logsタブ上部（Type/Trigger/Site Filter/AP Filterの並びの近く）に追加:

- 「過去7日分のイベントログを取得」ボタン
- クリック時:
  1. 確認ダイアログを表示（例: 「過去7日分のAPイベントを取得してCSV保存します。サイト数によっては
     数十秒かかる場合があります。実行しますか？」）
  2. `POST /api/ap-events/backfill?days=7` を実行
  3. 実行中はボタンをスピナー表示・操作不可に
  4. 完了後、結果をトースト等で表示（例: 「32件の新規イベントを取得しました（重複118件はスキップ）」）
  5. CSV Logs一覧を再取得して表示を更新

## 【4】Historyページ: バックフィルCSVの見分け

- ファイル名に`backfill`を含むものは、Triggerバッジを`manual-backfill`のような専用表示にする
  （既存の`auto`/`manual`バッジと区別できるように）

## 検証

実装後、`docker-compose up --build`で再ビルドし、以下を確認してください:

- ボタンクリックで過去7日分のイベントが取得され、`ap_events`テーブルに新規追加されること
- 01の自動保存で既に取得済みのイベントは重複登録されず、`duplicate_events`カウントに反映されること
- `ap_events_backfill_YYYYMMDD_HHMM_JST.csv`がHistoryに表示され、ダウンロードできること
- AP Detailの EVENTSセクションでバックフィルされたイベント（過去分）が表示されること

git操作は不要です。

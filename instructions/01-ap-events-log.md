# 01 - AP Events表示・CSVログ保存

## 推奨モデル: Sonnet

理由: スキーマ・APIエンドポイント・UI仕様はすべて確定済み（Mist APIの実レスポンスをcurlで事前検証済み）。
調査や設計のトレードオフ判断は発生せず、仕様通りに実装する機械的なタスクのため。

---

AP DetailページにAPのイベントログを表示し、HistoryのCSVログにも保存する機能を実装してください。
execute all commands automatically without stopping.

## 背景

Mist の Device Events Search API を使い、AP再起動（`AP_RESTARTED` / `AP_RESTART_BY_USER`）を
中心としたイベントログを取得・保存・表示する。Mist側の検索APIは約30日程度しか遡れないため、
自前でCSVログとして蓄積する価値がある。

事前に実機で以下を確認済み:
- `GET /api/v1/const/device_events` でAP関連イベントtypeを確認（`AP_RESTARTED`, `AP_RESTART_BY_USER`,
  `AP_RADAR_DETECTED`, `AP_PORT_DOWN`, `AP_CONFIG_CHANGED_BY_USER`, `AP_CONFIG_CHANGED_BY_RRM`,
  `AP_RRM_ACTION`, `AP_CONFIGURED` 等）
- `GET /sites/{site_id}/devices/events/search?type=AP_RADAR_DETECTED&duration=7d&limit=10` の実レスポンス構造:
  ```json
  {
    "reason": "radar-detected",
    "bandwidth": 20,
    "channel": 48,
    "type": "AP_RADAR_DETECTED",
    "device_type": "ap",
    "pre_bandwidth": 20,
    "mac": "a8f7d981e375",
    "ap": "a8f7d981e375",
    "timestamp": 1786385086,
    "org_id": "...",
    "site_id": "...",
    "pre_channel": 64,
    "band": "5"
  }
  ```
  フィールド構成はイベントtypeによって異なる（可変）。

## 【1】バックエンド: Mist APIクライアント

`mist/client.py` に以下を追加:

```python
async def get_site_device_events(self, site_id: str, duration: str = "1d", limit: int = 100) -> list[dict]:
    # GET /sites/{site_id}/devices/events/search?duration={duration}&limit={limit}
    # 必要ならpageでページネーション（totalがlimit超えの場合）
```

重要イベントtypeを定数化:

```python
RESTART_EVENT_TYPES = ["AP_RESTARTED", "AP_RESTART_BY_USER"]
NOTABLE_EVENT_TYPES = RESTART_EVENT_TYPES + ["AP_RADAR_DETECTED", "AP_PORT_DOWN"]
```

## 【2】DB: ap_events テーブル新規作成

```sql
CREATE TABLE ap_events (
    id INTEGER PRIMARY KEY,
    event_timestamp DATETIME,  -- Mistのtimestamp(epoch秒)を変換
    fetched_at DATETIME,       -- 自分側で取得した時刻
    site_id TEXT,
    site_name TEXT,
    ap_mac TEXT,
    ap_id TEXT,                -- ap_metrics経由で引ければ付与
    ap_name TEXT,
    event_type TEXT,
    reason TEXT,
    band TEXT,
    channel INTEGER,
    pre_channel INTEGER,
    bandwidth INTEGER,
    pre_bandwidth INTEGER,
    raw_json TEXT              -- レスポンス全体をJSON文字列で保存（type依存フィールド対応）
);
CREATE UNIQUE INDEX idx_ap_events_dedup ON ap_events(site_id, ap_mac, event_type, event_timestamp);
```

`migrate_db()` に登録。UNIQUE INDEXで重複取得時のINSERT OR IGNOREを可能にする。

## 【3】バックエンド: 取得タイミングとCSV保存

1時間ごとの自動ログ保存job（既存の`save_hourly_logs`）に追加:

- 監視対象全サイトに対し `get_site_device_events(site_id, duration="1h")` を実行
- 取得したイベントを `ap_events` テーブルにINSERT OR IGNORE（site_id+ap_mac+event_type+timestampで重複除外）
- 同タイミングで `ap_events_YYYYMMDD_HHMM_JST.csv` を `data/logs/` に保存
  - カラム: event_timestamp(JST変換), site_name, ap_name, ap_mac, event_type, reason, band, channel,
    pre_channel, bandwidth, pre_bandwidth

## 【4】バックエンド: APIエンドポイント

```
GET /api/aps/{ap_id}/events?hours=24
  → ap_events テーブルから、該当ap_macの過去N時間分を新しい順で返す
  → restart系イベントは is_restart: true をレスポンスに付加
```

## 【5】フロントエンド: AP DetailにEVENTSセクション追加

CONFIG CHANGE HISTORYの下に追加（既存のEVENTSセクションがすでにあれば拡張）:

- `GET /api/aps/{ap_id}/events?hours=24` をSWRで取得
- 時間範囲セレクター: 24h / 7d / 30d
- テーブル表示: 時刻 | Type | Reason | 詳細（band/channel変化等）
- **AP_RESTARTED / AP_RESTART_BY_USER は赤バッジで強調表示**
- AP_RADAR_DETECTED（DFS）はオレンジバッジ
- その他はtypeをそのままバッジ表示（グレー）
- データなしの場合は「イベントはありません」を表示

## 【6】Historyページにap_events対応

- ファイル名プレフィックス `ap_events_` → Typeバッジ `AP Events`
- Typeドロップダウンに追加

## 検証

実装後、`docker-compose up --build` で再ビルドし、以下を確認してください:

- AP DetailでAP_RADAR_DETECTEDの履歴（事前確認した6件）が表示されること
- HistoryにAP EventsのCSVが保存されること

git操作は不要です。

# 03 - backfill_ap_events 重複カウント不整合の調査

## 推奨モデル: Opus

理由: 「1600件」という数字の出どころが不明で、原因を1つに絞り込めていない（カウントロジックのバグ、
ページネーション処理のバグ、集計方法のミスなど複数の仮説がある）。まだ仕様が確定していない
原因不明の調査タスクのため、コードを読み解いて筋道を立てる判断力が必要。原因が特定でき次第、
修正自体は02と同様にSonnetで対応可能。

---

「過去7日分のイベントログを取得」ボタンで「0件の新規イベントを取得しました（重複1600件はスキップ）」
と表示されたが、実際の`ap_events`テーブルの件数は213件しかなく、矛盾している。

以下を調査してください（まだ修正は不要です）。execute all commands automatically without stopping.

## 背景

- `POST /api/ap-events/backfill?days=7` を実行
- レスポンス（またはトースト表示）で「新規0件・重複1600件」と表示された
- しかし `sqlite3 data/mist.db "SELECT COUNT(*) FROM ap_events;"` は213件
- 監視対象は4サイト程度の小規模環境

矛盾の可能性:
- カウントロジックが実際のINSERT結果ではなく取得件数を単純累計している
- ページネーション処理で同じページを複数回取得している（無限ループ・ページ進行条件のミス）
- サイト数×取得件数の単純集計になっている

## 【1】`backfill_ap_events()` のカウントロジックを確認

`scheduler.py` の `backfill_ap_events()` と、01/02で共通化した `_store_ap_events()`（または同等の関数）
を確認し、以下を報告してください:

- `new_events` と `duplicate_events` はどうやってカウントしているか
  （INSERTのrowcountを見ているか、取得件数を単純に累計しているだけか）
- ページネーション処理で、同じページを複数回取得してしまうバグがないか
  （無限ループやページ進行条件のミス）
- サイト数 × 取得件数 の単純集計になっていないか

## 【2】実際のMist APIレスポンス件数との照合

```bash
source .env && curl -s \
  -H "Authorization: Token $MIST_API_TOKEN" \
  "$MIST_BASE_URL/sites/bcb8f2c8-5cdb-4c3d-86f7-fe1c3a24c1ed/devices/events/search?duration=7d&limit=100" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'total: {d.get(\"total\")}, 返却件数: {len(d.get(\"results\",[]))}')"
```

この値を監視対象全サイト分合計したものと、backfillが抱えていた「1600」という数字を比較してください。

## 【3】バックエンドログの確認

```bash
docker compose logs backend --tail=100 | grep -i "backfill\|ap_events"
```

## 報告してほしいこと

この3点の調査結果のみ報告してください。修正はまだ行わないでください。
原因の仮説が複数残る場合は、それぞれの可能性と、切り分けに必要な追加調査を提示してください。

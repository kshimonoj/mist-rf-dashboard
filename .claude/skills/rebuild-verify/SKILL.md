---
name: rebuild-verify
description: Rebuild the Mist Dashboard with Docker Compose and verify both services are healthy. Use after code changes to confirm the running app works.
disable-model-invocation: true
---

# rebuild-verify

ローカルの変更を Docker で再ビルドし、バックエンド／フロントエンドの疎通を確認する。
ユーザーが `/rebuild-verify` を実行したときの手順。

## 手順

1. **型チェック（フロント変更がある場合）**
   ```bash
   cd frontend && npx tsc --noEmit
   ```
   `node_modules` が無ければ `npm install` 後に再実行。エラーがあればここで報告して停止。

2. **構文チェック（バックエンド変更がある場合）**
   ```bash
   cd backend && python3 -m py_compile <変更した .py>
   ```

3. **再ビルド & 起動（デタッチ）**
   ```bash
   docker compose up --build -d
   ```

4. **疎通確認**
   ```bash
   docker compose ps
   curl -s localhost:8008/health
   curl -s -o /dev/null -w "frontend HTTP %{http_code}\n" localhost:3007/
   ```
   - backend は `{"status":"ok"}`、frontend は `200` を期待。

5. **変更箇所のスモークテスト（任意）**
   変更した API があれば該当エンドポイントを `curl` で確認する
   （例: `curl -s localhost:8008/api/sites`、新規ルーターは代表的な GET を叩く）。

6. **後片付け**
   `npx tsc` 実行で `frontend/next-env.d.ts` `frontend/tsconfig.tsbuildinfo` `frontend/package-lock.json` が生成された場合は削除する（コミット対象外）。

## 報告

各ステップの結果（型チェック / ビルド成否 / health / HTTP ステータス / スモークテスト）を簡潔にまとめて報告する。失敗があれば該当ログを添えて停止する。

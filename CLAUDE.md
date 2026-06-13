# Mist Dashboard — プロジェクト指針

Juniper Mist の AP / クライアントを監視する RF モニタリングダッシュボード。
バックエンド: Python / FastAPI + SQLAlchemy + Pydantic + APScheduler（SQLite）。
フロントエンド: Next.js 14 + TypeScript + Tailwind + recharts + SWR。
実行は Docker Compose（backend: `localhost:8008` / frontend: `localhost:3007`）。

## 検証フロー

コード変更後は以下の順で確認する:

1. **フロントの型チェック**: `cd frontend && npx tsc --noEmit`
   - `node_modules` はローカルに無い場合がある。無ければ `npm install` 後に実行。
   - ESLint は未設定（`next lint` は対話プロンプトになるため CI では使わない）。
2. **バックエンドの構文チェック**: `cd backend && python3 -m py_compile <変更ファイル>`
3. **反映（再ビルド）**: `docker compose up --build -d`
4. **疎通確認**:
   - backend: `curl -s localhost:8008/health` → `{"status":"ok"}`
   - frontend: `curl -s -o /dev/null -w "%{http_code}" localhost:3007/` → `200`
   - 必要なら該当 API を `curl` で確認（例: `curl -s localhost:8008/api/sites`）

## 規約

- **MAC アドレス**: DB 保存・照合ともに **コロンなし小文字** に正規化する（`:` `-` を除去）。
- **新規 API の追加手順（3点セット）**:
  1. `backend/models.py` にテーブル定義（必要なら）
  2. `backend/routers/<name>.py` にルーターを実装
  3. `backend/main.py` で `from routers import ...` と `app.include_router(...)` に登録
  - 新テーブルは `Base.metadata.create_all`（起動時）で自動作成される。既存テーブルへの列追加は `database.py` の `migrate_db()` に追記する。
- **日時**: バックエンドは `utils.fmt_dt` で UTC（`Z` 付き ISO）を返す。フロントは `lib/time.ts` でブラウザ／指定 TZ にローカル変換して表示する。
- **コーディングスタイル**: 全体書き換えより差分修正を優先。既存ファイルの命名・コメント密度・イディオムに合わせる。

## 注意事項

- **秘匿情報を絶対にコミット／Push しない**: API Key・トークン・個人特定情報。`.env*` と `data/` は秘匿対象。
  - `.env*` ファイルの編集は PreToolUse フックでブロックされる（`.claude/hooks/block-env-edits.py`）。
- **ビルド生成物はコミットしない**: `frontend/next-env.d.ts` / `frontend/tsconfig.tsbuildinfo` / `frontend/package-lock.json`（`.gitignore` 済み）。`npx tsc` 実行後に生成された場合は追加しない。

## Git 操作

- **git commit / push / ブランチ作成などの git 操作は、明示的に指示された場合のみ実行する。**
- 自動で push まで進めない。コミットメッセージは英語で簡潔に。
- push は指定ブランチ（通常 `main`）にのみ行う。ブランチ作成が必要と判断した場合は実行前に報告して確認を取る。

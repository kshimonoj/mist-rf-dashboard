#!/usr/bin/env python3
"""PreToolUse hook: block edits to secret .env files.

`.env` と `.env.*`（例: .env.home, .env.kyobashi）への Edit/Write をブロックする。
非秘匿のテンプレート `.env.example` は許可する。
exit code 2 で stderr を返すと Claude Code はツール実行を拒否する。
"""
import json
import os
import re
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

path = (data.get("tool_input") or {}).get("file_path", "") or ""
base = os.path.basename(path)

if re.match(r"^\.env(\..+)?$", base) and base != ".env.example":
    sys.stderr.write(
        f"Blocked: editing secret file '{base}' is not allowed (contains API keys / secrets). "
        f"Update it manually outside Claude, or edit .env.example for templates.\n"
    )
    sys.exit(2)

sys.exit(0)

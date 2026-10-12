#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

if [[ ! -d web/node_modules ]]; then
  npm --prefix web ci
fi

server_pid=""
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
  uv run --no-sync qrclaw serve --host 127.0.0.1 --port 8765 &
  server_pid=$!
  for _ in {1..30}; do
    if curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      echo "QRClaw API 启动失败" >&2
      exit 1
    fi
    sleep 1
  done
  if ! curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
    echo "QRClaw API 未就绪" >&2
    exit 1
  fi
fi

npm --prefix web run dev -- --host 127.0.0.1

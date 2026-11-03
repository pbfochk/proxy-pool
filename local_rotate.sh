#!/usr/bin/env bash
# 本地代理轮换网关：从服务器同步代理 -> mubeng 自动轮换 -> 工具指向 127.0.0.1:10888
#
# 用法:
#   export PROXY_POOL_TOKEN=change-me
#   export PROXY_POOL_SERVER=http://127.0.0.1:32580
#   ./local_rotate.sh                      # 默认 http 代理，每 1 个请求换一次 IP
#   PROTO=socks5 ./local_rotate.sh         # 用 socks5 代理（交给 mubeng，不是 rotate_gateway）
#   ROTATE_EVERY=10 ./local_rotate.sh      # 每 10 个请求换一次
#   LOCAL_PORT=9999 ./local_rotate.sh      # 换网关端口
#
# 也可把 KEY=VALUE 写进 ./.env 或 ~/.proxies/.env。仓库忽略 .env。
# 指定其它文件: PROXY_POOL_ENV=/path/to/env ./local_rotate.sh

set -u

if [ -n "${PROXY_POOL_ENV:-}" ]; then
  ENV_FILE="$PROXY_POOL_ENV"
elif [ -f .env ]; then
  ENV_FILE=".env"
elif [ -f "$HOME/.proxies/.env" ]; then
  ENV_FILE="$HOME/.proxies/.env"
else
  ENV_FILE=""
fi
if [ -n "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

TOKEN="${PROXY_POOL_TOKEN:?请设置 PROXY_POOL_TOKEN，或写入 .env / ~/.proxies/.env}"
SERVER="${PROXY_POOL_SERVER:-http://127.0.0.1:32580}"
SERVER="${SERVER%/}"
LOCAL_PORT="${LOCAL_PORT:-10888}"
ROTATE_EVERY="${ROTATE_EVERY:-1}"
SYNC_INTERVAL="${SYNC_INTERVAL:-300}"
PROXY_FILE="${PROXY_FILE:-$HOME/.proxies/live.txt}"
PROTO="${PROTO:-http}"

if [ "$PROTO" = "socks5" ]; then
  PREFIX="${SOCKS5_PREFIX:-socks5://}"
  SYNC_URL="$SERVER/all?type=socks5"
  PROTO_LABEL="socks5"
else
  PREFIX="http://"
  SYNC_URL="$SERVER/all"
  PROTO_LABEL="http"
fi

command -v curl >/dev/null 2>&1 || { echo "需要 curl" >&2; exit 1; }
command -v awk >/dev/null 2>&1 || { echo "需要 awk" >&2; exit 1; }
command -v mubeng >/dev/null 2>&1 || {
  echo "需要 mubeng。macOS: brew install mubeng ；其它平台见 https://github.com/kitabisa/mubeng/releases" >&2
  exit 1
}

mkdir -p "$(dirname "$PROXY_FILE")"
touch "$PROXY_FILE"

sync() {
  local tmp count
  tmp=$(mktemp "${PROXY_FILE}.XXXXXX") || {
    echo "[$(date '+%H:%M:%S')] 无法创建临时文件，保留上一份列表" >&2
    return 0
  }
  set -o pipefail
  if ! curl -fsS --max-time 20 -H "X-Token: $TOKEN" "$SYNC_URL" \
      | awk -v p="$PREFIX" 'NF{gsub(/\r/, ""); print p $0}' > "$tmp"; then
    set +o pipefail
    echo "[$(date '+%H:%M:%S')] 同步失败，保留上一份列表: $SYNC_URL" >&2
    rm -f "$tmp"
    return 0
  fi
  set +o pipefail
  count=$(wc -l < "$tmp" | tr -d ' ')
  # 覆盖原 inode，方便 mubeng -w 观察到同一次打开的文件被更新。
  cat "$tmp" > "$PROXY_FILE"
  rm -f "$tmp"
  echo "[$(date '+%H:%M:%S')] 已同步 $count 条 $PROTO_LABEL 代理 -> $PROXY_FILE"
}

echo "== 本地代理轮换网关 ($PROTO_LABEL) =="
sync

(
  trap '' INT TERM
  while true; do
    sleep "$SYNC_INTERVAL"
    sync
  done
) &
SYNC_PID=$!

cleanup() {
  # 只结束本脚本拉起的同步子进程。不要用 pkill -f，那会误伤 SSH 会话。
  for pid in $(pgrep -P "$SYNC_PID" 2>/dev/null); do
    kill -9 "$pid" 2>/dev/null
  done
  kill -9 "$SYNC_PID" 2>/dev/null
  exit 0
}
trap cleanup INT TERM EXIT

echo "轮换网关已启动: http://127.0.0.1:$LOCAL_PORT  (每 $ROTATE_EVERY 个请求换IP, 随机, 失败自动重试)"
echo "每 $SYNC_INTERVAL 秒自动同步一次服务器代理 (mubeng -w 自动重载)"
echo "Ctrl+C 停止"
echo ""

mubeng -a "127.0.0.1:$LOCAL_PORT" -f "$PROXY_FILE" -r "$ROTATE_EVERY" -m random -w --rotate-on-error

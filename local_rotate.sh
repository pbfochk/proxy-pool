#!/usr/bin/env bash
# 本地代理轮换网关：从服务器同步代理 -> mubeng 自动轮换 -> 工具指向 127.0.0.1:10888
#
# 用法:
#   ./local_rotate.sh              # 默认每 1 个请求换一次 IP
#   ROTATE_EVERY=10 ./local_rotate.sh   # 每 10 个请求换一次
#   LOCAL_PORT=9999 ./local_rotate.sh   # 换网关端口

set -u

TOKEN="YOUR_TOKEN_HERE"
SERVER="http://YOUR_SERVER_IP:YOUR_PORT"
LOCAL_PORT="${LOCAL_PORT:-10888}"
ROTATE_EVERY="${ROTATE_EVERY:-1}"
SYNC_INTERVAL="${SYNC_INTERVAL:-300}"
PROXY_FILE="$HOME/.proxies/live.txt"

mkdir -p "$HOME/.proxies"

sync() {
  local count
  count=$(curl -s -H "X-Token: $TOKEN" "$SERVER/all" | sed 's/^/http:\/\//' | tee "$PROXY_FILE" | wc -l | tr -d ' ')
  echo "[$(date '+%H:%M:%S')] 已同步 $count 条代理 -> $PROXY_FILE"
}

echo "== 本地代理轮换网关 =="
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

#!/usr/bin/env bash
# 一键部署常驻代理池到 Linux（Ubuntu/Debian）。
# 仓库只提交 *.service.example。本脚本把它装到 /etc/systemd/system/。
# 单元里若仍是 CHANGE_ME，会生成随机 token；重复安装会沿用已有 token，不重新打印。
set -euo pipefail

DEPLOY_DIR="/opt/proxy-pool"
GW_DIR="/opt/proxy-gateway"
PORT="32580"
INTERVAL="300"
UNIT="/etc/systemd/system/proxy-pool.service"
GW_UNIT="/etc/systemd/system/proxy-rotate.service"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
err()  { echo -e "${RED}[-]${NC} $*"; }

token_ok() {
  printf '%s' "$1" | grep -Eq '^[A-Za-z0-9._~+-]{8,128}$'
}

write_token() {
  local unit="$1"
  UNIT_PATH="$unit" PROXY_POOL_NEW_TOKEN="$TOKEN" python3 -c 'import os; from pathlib import Path; p=Path(os.environ["UNIT_PATH"]); t=os.environ["PROXY_POOL_NEW_TOKEN"]; p.write_text(p.read_text().replace("CHANGE_ME", t))'
}

[ "$(id -u)" -eq 0 ] || { err "请用 root 运行"; exit 1; }

log "安装 python3 与依赖..."
apt-get update -y >/dev/null
apt-get install -y python3 python3-pip >/dev/null
if ! apt-get install -y python3-requests >/dev/null 2>&1; then
  pip3 install -q requests || { err "无法安装 requests"; exit 1; }
fi
if ! python3 -c 'import requests' >/dev/null 2>&1; then
  pip3 install -q requests || { err "无法安装 requests"; exit 1; }
fi
if apt-get install -y python3-socks >/dev/null 2>&1 || pip3 install -q PySocks; then
  python3 -c 'import socks' >/dev/null 2>&1 || log "未安装 PySocks。SOCKS5 池不可用时，给 proxy_server.py 加 --no-socks5"
else
  log "未安装 PySocks。SOCKS5 池不可用时，给 proxy_server.py 加 --no-socks5"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "${SCRIPT_DIR}/proxy_server.py" ] || { err "缺少 proxy_server.py"; exit 1; }

log "复制脚本到 ${DEPLOY_DIR} ..."
mkdir -p "${DEPLOY_DIR}"
cp "${SCRIPT_DIR}/proxy_server.py" "${DEPLOY_DIR}/"

EXISTING_TOKEN=""
if [ -f "${UNIT}" ]; then
  EXISTING_TOKEN="$(sed -n 's/.*--token[[:space:]]\([^[:space:]]*\).*/\1/p' "${UNIT}")"
  EXISTING_TOKEN="${EXISTING_TOKEN%%$'\n'*}"
  if [ "${EXISTING_TOKEN}" = "CHANGE_ME" ] || [ -z "${EXISTING_TOKEN}" ]; then
    EXISTING_TOKEN=""
  elif ! token_ok "${EXISTING_TOKEN}"; then
    err "已安装单元中的 token 无法安全写回（仅允许字母数字和 . _ ~ + -，长度 8–128）。请先整理 ${UNIT}"
    exit 1
  fi
fi

if [ -f "${SCRIPT_DIR}/proxy-pool.service" ]; then
  cp "${SCRIPT_DIR}/proxy-pool.service" "${UNIT}"
elif [ -f "${SCRIPT_DIR}/proxy-pool.service.example" ]; then
  cp "${SCRIPT_DIR}/proxy-pool.service.example" "${UNIT}"
else
  err "缺少 proxy-pool.service.example"
  exit 1
fi

sed -i "s/--port [0-9]*/--port ${PORT}/" "${UNIT}"
sed -i "s/--interval [0-9]*/--interval ${INTERVAL}/" "${UNIT}"

TOKEN=""
MINTED=0
if [ -n "${EXISTING_TOKEN}" ]; then
  TOKEN="${EXISTING_TOKEN}"
  log "沿用已安装单元中的 token，不重新生成、不打印。"
elif grep -q 'CHANGE_ME' "${UNIT}"; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  MINTED=1
  log "已生成随机 token。请立即记录，并在事后清理 shell 历史。"
  log "token: ${TOKEN}"
else
  log "单元里没有占位符，也没有读到旧 token。请自行确认鉴权配置。"
fi

if [ -n "${TOKEN}" ]; then
  token_ok "${TOKEN}" || { err "token 含有无法安全写入单元文件的字符"; exit 1; }
  write_token "${UNIT}"
  ( umask 077
    printf 'PROXY_POOL_TOKEN=%s\nPROXY_POOL_SERVER=http://127.0.0.1:%s\n' "${TOKEN}" "${PORT}" > "${DEPLOY_DIR}/.env"
  )
  chmod 600 "${DEPLOY_DIR}/.env"
  log "token 已写入 ${DEPLOY_DIR}/.env（权限 600）"
fi

if [ -f "${SCRIPT_DIR}/rotate_gateway.py" ]; then
  mkdir -p "${GW_DIR}"
  cp "${SCRIPT_DIR}/rotate_gateway.py" "${GW_DIR}/"
  if [ -f "${SCRIPT_DIR}/proxy-rotate.service.example" ]; then
    cp "${SCRIPT_DIR}/proxy-rotate.service.example" "${GW_UNIT}"
    if [ -n "${TOKEN}" ]; then
      write_token "${GW_UNIT}"
    elif grep -q 'CHANGE_ME' "${GW_UNIT}"; then
      log "proxy-rotate.service 仍是占位符。启用前请填入与代理池相同的 token。"
    fi
    log "已安装可选轮换网关，默认不启用: systemctl enable --now proxy-rotate"
  fi
fi

log "启用并启动代理池..."
systemctl daemon-reload
systemctl enable proxy-pool
systemctl restart proxy-pool

sleep 3
echo ""
log "部署完成，服务状态:"
systemctl --no-pager status proxy-pool --lines=5 || true
echo ""
if [ "${MINTED}" -eq 1 ]; then
  SHOW_TOKEN="${TOKEN}"
else
  SHOW_TOKEN='<你的 token>'
fi
log "取用方式（首次刷新可能要几分钟，health 里数量为 0 属正常）:"
echo "  随机 HTTP:   curl -s -H \"X-Token: ${SHOW_TOKEN}\" http://127.0.0.1:${PORT}/"
echo "  随机 SOCKS5: curl -s -H \"X-Token: ${SHOW_TOKEN}\" \"http://127.0.0.1:${PORT}/?type=socks5\""
echo "  全部 HTTP:   curl -s -H \"X-Token: ${SHOW_TOKEN}\" http://127.0.0.1:${PORT}/all"
echo "  健康检查:    curl -s -H \"X-Token: ${SHOW_TOKEN}\" http://127.0.0.1:${PORT}/health"
echo "  手动刷新:    curl -s -H \"X-Token: ${SHOW_TOKEN}\" http://127.0.0.1:${PORT}/refresh"
echo "  查看日志:    journalctl -u proxy-pool -f"

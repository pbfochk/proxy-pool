#!/usr/bin/env bash
set -euo pipefail

# 一键部署常驻代理池服务到 Linux (Ubuntu/Debian) 服务器

DEPLOY_DIR="/opt/proxy-pool"
PORT="32580"
INTERVAL="300"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
err()  { echo -e "${RED}[-]${NC} $*"; }

[ "$(id -u)" -eq 0 ] || { err "请用 root 运行"; exit 1; }

log "安装 python3 与依赖..."
apt-get update -y >/dev/null
apt-get install -y python3 python3-pip >/dev/null
pip3 install -q requests || apt-get install -y python3-requests

log "复制脚本到 ${DEPLOY_DIR} ..."
mkdir -p "${DEPLOY_DIR}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "${SCRIPT_DIR}/proxy_server.py" "${DEPLOY_DIR}/"
cp "${SCRIPT_DIR}/proxy-pool.service" /etc/systemd/system/

# 写入端口与间隔到 service 文件
sed -i "s/--port [0-9]*/--port ${PORT}/" /etc/systemd/system/proxy-pool.service
sed -i "s/--interval [0-9]*/--interval ${INTERVAL}/" /etc/systemd/system/proxy-pool.service

log "启用并启动服务..."
systemctl daemon-reload
systemctl enable proxy-pool
systemctl restart proxy-pool

sleep 3
echo ""
log "部署完成，服务状态:"
systemctl --no-pager status proxy-pool --lines=5 || true
echo ""
log "取用方式:"
echo "  随机取一个: curl -s http://127.0.0.1:${PORT}/"
echo "  取全部:     curl -s http://127.0.0.1:${PORT}/all"
echo "  健康检查:   curl -s http://127.0.0.1:${PORT}/health"
echo "  手动刷新:   curl -s http://127.0.0.1:${PORT}/refresh"
echo "  查看日志:   journalctl -u proxy-pool -f"

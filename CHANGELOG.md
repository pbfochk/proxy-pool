# Changelog

本文件记录对外可见的行为变化。版本号遵循语义化版本。

## [0.2.0] - 2026-11-03

### 新增

- `rotate_gateway.py`：服务端本地 HTTP 轮换网关，纯标准库。默认监听 `127.0.0.1:7890`，每个新连接换一个出口；CONNECT 被拒绝或建连失败时换下一个出口，直到超时才向客户端返回 502。
- `proxy-rotate.service.example`：可选 systemd 单元，默认不启用。
- 代理池同时维护 HTTP 与 SOCKS5。API 用 `?type=http|socks5` 选择；`/health` 与 `/stats` 返回 `http_alive`、`socks5_alive`、`last_refresh`。
- `--https-target`：额外做一次 HTTPS 请求，滤掉无法建立隧道的节点。
- `--socks5-local-dns`：SOCKS5 校验改用本地 DNS（`socks5://`）。默认仍是远程 DNS（`socks5h://`）。
- `--no-socks5`：没有 PySocks / `python3-socks` 时只跑 HTTP 池。
- `requirements.txt`、`LICENSE`（MIT）、`.env.example`、`docs/operations.md`。

### 变更

- `local_rotate.sh` 从环境变量 `PROXY_POOL_TOKEN`、`PROXY_POOL_SERVER` 读取地址和 token，可用 `PROTO=socks5` 同步 SOCKS5 列表给 mubeng。
- `install.sh` 从 `*.service.example` 安装单元。遇到 `CHANGE_ME` 时生成随机 token，并写入 `/opt/proxy-pool/.env`（权限 600）。重复安装沿用已有 token。可选网关单元会装上，但不会 enable。
- 仓库不再提交真实的 `proxy-pool.service`。示例单元使用占位符 `CHANGE_ME`。

### 安全

- 增加 `.gitignore`，忽略 `.env`、代理列表、`__pycache__`、`.DS_Store` 和真实 `*.service` 文件。
- 轮换网关在绑定非回环地址时打印警告。调试日志由 `GW_DEBUG=1` 或 `--debug` 打开，日志里不输出 token。
- 未传入 `--token` 时，代理池和网关读取 `PROXY_POOL_TOKEN`。

### 修复

- `local_rotate.sh` 用 `awk` 给代理行加 `http://` 或 `socks5://`。原先若用 `sed` 且分隔符是 `/`，协议前缀里的斜杠会让同步命令直接失败。
- 同步失败时保留上一份列表，避免 mubeng 热加载到一份空文件。
- 停止脚本时只结束自己的同步子进程。

### 不兼容

- `/health` 不再返回 `alive`。请改读 `http_alive` 与 `socks5_alive`。

## [0.1.0] - 2026-09-09

- 初始版本：HTTP 代理池服务、systemd 示例、本机 mubeng 轮换脚本。

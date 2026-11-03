# Free Proxy Pool

**当前版本：0.2.0**

在自己的机器上维护一份经过校验的公开代理，并通过本地网关轮换出口。适合已获授权的连通性测试和频率受限场景下的出口轮换。免费代理质量波动大，防护严格的目标需要换成住宅代理，替换抓取来源即可，API 形状可以保持不变。

仅用于已获授权的测试。

## 架构

```
工具
  │
  ├─ 笔记本：local_rotate.sh → mubeng 127.0.0.1:10888
  │            每 N 个请求换一个出口，并定时拉取 /all
  │
  └─ 服务器（可选）：rotate_gateway.py 127.0.0.1:7890
               每个新连接换一个出口，CONNECT 失败自动换下一个
               │
               ▼
        proxy_server.py :32580
          ├─ HTTP 池     proxies.txt
          └─ SOCKS5 池   socks5_proxies.txt
```

池服务负责抓取、校验，并按 token 发放 `ip:port`。它的默认监听是 `0.0.0.0`，靠 token 和防火墙保护。两个网关都只给本机用：mubeng 由脚本绑在 `127.0.0.1`，`rotate_gateway.py` 的默认监听也是 `127.0.0.1`，并且没有鉴权。

## 仓库文件

| 文件 | 作用 |
|------|------|
| `proxy_server.py` | 常驻双池服务 |
| `proxy_pool.py` | 单次抓取 HTTP 代理；`--serve` 只监听 `127.0.0.1`，没有鉴权 |
| `rotate_gateway.py` | 可选的服务端轮换网关（标准库） |
| `local_rotate.sh` | 笔记本上的 mubeng 同步与轮换 |
| `install.sh` | 从 example 单元安装到 systemd |
| `proxy-pool.service.example` | 代理池单元，token 为 `CHANGE_ME` |
| `proxy-rotate.service.example` | 可选网关单元，默认不要 enable |
| `requirements.txt` | `requests`；SOCKS5 另需 PySocks |
| `docs/operations.md` | 安装、校验开关和排障 |

## 环境

- 服务端：Python 3.9+、`requests`。要校验 SOCKS5 时再装 PySocks，或系统包 `python3-socks`。
- 网关：`rotate_gateway.py` 没有第三方依赖。
- 笔记本：`curl`、`awk`、[mubeng](https://github.com/kitabisa/mubeng/releases)。macOS 可以用 `brew install mubeng`。

```bash
python3 -m pip install -r requirements.txt
```

## 安装服务端

在 Ubuntu/Debian 上，用 root 运行：

```bash
sudo bash install.sh
```

脚本把 `proxy-pool.service.example` 装到 `/etc/systemd/system/proxy-pool.service`。单元里若还是 `CHANGE_ME`，会生成 32 位十六进制 token，打印一次，并写入 `/opt/proxy-pool/.env`（权限 600）。重复安装沿用已有 token，不再打印。可选的 `proxy-rotate.service` 会装上，但保持不启用。

手工启动时：

```bash
export PROXY_POOL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
python3 proxy_server.py \
  --bind 0.0.0.0 \
  --port 32580 \
  --interval 300 \
  --anonymity \
  --token "$PROXY_POOL_TOKEN" \
  --https-target "https://example.com"
```

`--https-target` 留空时只做 HTTP 校验。示例单元为了能先启动，写的是空字符串；准备拿来转发 HTTPS 之前，换成真实 HTTPS 地址并重启。详见 [docs/operations.md](docs/operations.md)。

## API

除特别说明外都要鉴权：请求头 `X-Token`，或查询参数 `token`。`type` 只接受 `http`（默认）和 `socks5`。

```bash
export PROXY_POOL_TOKEN="change-me"
export PROXY_POOL_SERVER="http://127.0.0.1:32580"

curl -s -H "X-Token: ${PROXY_POOL_TOKEN}" "${PROXY_POOL_SERVER}/health"
curl -s -H "X-Token: ${PROXY_POOL_TOKEN}" "${PROXY_POOL_SERVER}/"
curl -s -H "X-Token: ${PROXY_POOL_TOKEN}" "${PROXY_POOL_SERVER}/all"
curl -s -H "X-Token: ${PROXY_POOL_TOKEN}" "${PROXY_POOL_SERVER}/?type=socks5"
curl -s -H "X-Token: ${PROXY_POOL_TOKEN}" "${PROXY_POOL_SERVER}/all?type=socks5"
curl -s -H "X-Token: ${PROXY_POOL_TOKEN}" "${PROXY_POOL_SERVER}/refresh"
```

| 路径 | 返回 |
|------|------|
| `/` | 随机一条 `ip:port`，末尾换行 |
| `/all` | 全部可用代理，每行一条 |
| `/health`、`/stats` | `{"http_alive":N,"socks5_alive":N,"last_refresh":unix}` |
| `/refresh` | 异步刷新，立刻返回 `ok` |
| 其它路径 | 与 `/` 相同，随机一条 |

0.2.0 起健康检查不再使用 `alive` 字段。鉴权失败返回 `401`，`type` 非法返回 `400`。

## 环境变量

| 变量 | 默认 | 谁在读 |
|------|------|--------|
| `PROXY_POOL_TOKEN` | 空 | 池、网关、`local_rotate.sh`。池在命令行和该变量都为空时自动生成 |
| `PROXY_POOL_SERVER` | `http://127.0.0.1:32580` | 网关、`local_rotate.sh` |
| `PROXY_POOL_ENV` | 先 `./.env`，再 `~/.proxies/.env` | `local_rotate.sh` |
| `LOCAL_PORT` | `10888` | mubeng |
| `ROTATE_EVERY` | `1` | 每 N 个请求换一次出口 |
| `SYNC_INTERVAL` | `300` | 拉取列表的间隔（秒） |
| `PROTO` | `http` | `socks5` 时拉取 `/all?type=socks5` 并加上 `socks5://` |
| `SOCKS5_PREFIX` | `socks5://` | 仅 `PROTO=socks5` |
| `GW_DEBUG` | 空 | 设为 `1` 时网关打印每次换出口的结果 |

`.env.example` 是模板。填好的 `.env` 已被忽略，不要提交。

## 笔记本上的 mubeng

```bash
export PROXY_POOL_TOKEN="change-me"
export PROXY_POOL_SERVER="http://YOUR_SERVER:32580"
./local_rotate.sh
ROTATE_EVERY=10 ./local_rotate.sh
PROTO=socks5 ./local_rotate.sh
```

脚本把列表写到 `~/.proxies/live.txt`，每行带协议前缀，然后执行：

```bash
mubeng -a 127.0.0.1:10888 -f "$HOME/.proxies/live.txt" -r 1 -m random -w --rotate-on-error
```

同步失败时保留上一份文件。退出时只结束自己的同步子进程。

工具把代理指到本机网关即可，例如 HTTP 代理 `127.0.0.1:10888`。扫描类工具通常 `ROTATE_EVERY=1`；需要保持会话时再加大间隔。

## 服务端轮换网关

```bash
export PROXY_POOL_TOKEN="change-me"
python3 rotate_gateway.py --listen 127.0.0.1 --port 7890 --pool http://127.0.0.1:32580
```

- 默认 `--listen 127.0.0.1`。若改成其它地址，进程会打印警告。保持回环，远程使用走 SSH `-L`。
- 每个新 TCP 连接从池里取一个出口。CONNECT 遇到拒绝、超时或对端提前关闭时换下一个，30 秒内仍失败才返回 `502`。
- 普通 HTTP 绝对 URI 在 TCP 连接失败时换出口。一旦连上就把字节转发给客户端。
- `--debug` 与 `GW_DEBUG=1` 相同。
- `--type socks5` 只切换列表来源。网关向上游说的仍是 HTTP CONNECT。SOCKS5 出口用上一节的 `PROTO=socks5`。

启用已安装的单元：

```bash
sudo systemctl enable --now proxy-rotate
```

## 服务端参数

```bash
python3 proxy_server.py --help
```

| 参数 | 默认 | 含义 |
|------|------|------|
| `--port` | `8888`（示例单元为 `32580`） | API 端口 |
| `--bind` | `0.0.0.0` | API 监听地址 |
| `--interval` | `300` | 刷新间隔（秒） |
| `--target` | `http://httpbin.org/ip` | HTTP 校验 URL |
| `--https-target` | 空 | 非空时再请求一次该 HTTPS URL |
| `--timeout` | `5` | 读超时；连接超时固定约 3 秒 |
| `--threads` | `300` | 并发校验数 |
| `--anonymity` | 关 | 响应正文含代理自身 IP 时丢弃 |
| `--output` | `proxies.txt` | HTTP 列表；SOCKS5 写到 `socks5_` 前缀的文件 |
| `--token` | 空 | 空则读 `PROXY_POOL_TOKEN`，仍空则生成 |
| `--no-socks5` | 关 | 跳过 SOCKS5 池 |
| `--socks5-local-dns` | 关 | SOCKS5 用 `socks5://` 做本地 DNS |

校验结果会和磁盘上的旧列表合并后再测，重启后恢复会快一些。`/refresh` 在上一轮还没结束时不会叠第二轮。

## 安全

- 仓库里的 token 位置只保留 `CHANGE_ME` 或 `change-me`。真实 token、SSH 口令、私钥和填好的 `.env` 都不要提交。真实单元文件匹配 `*.service`，已被 `.gitignore` 忽略。
- 轮换网关只监听 `127.0.0.1`。它没有鉴权。
- 代理池若监听所有网卡，token 就是门禁。用防火墙收紧来源，并保留长随机 token。
- 给工具设置 `HTTP_PROXY` / `HTTPS_PROXY` 时，同时设置 `NO_PROXY`（以及小写的 `no_proxy`），至少包含 `localhost,127.0.0.1,::1`，并加上模型 API 和其它会携带密钥的域名。
- `--socks5-local-dns` 让 DNS 留在校验机上。示例单元打开了它，用来换更多可用节点；不能接受时从单元里删掉。
- 启动日志会打印 API token，便于取回自动生成的值。谁能读 journal，谁就能看到它。
- `proxy_pool.py --serve` 没有鉴权，绑定在 `127.0.0.1`。

## 现场容易踩的点

这些是部署时反复出现的情况，处理办法都在 [docs/operations.md](docs/operations.md)。

1. **假代理。** TCP 可连、HTTP 也是 200，出口 IP 却仍是服务器。加上 `--https-target`。
2. **CONNECT 403。** 企业出口（如 Zscaler 一类的拦截）会拒绝隧道。网关会换下一个出口，而不是在第一次 403 时就把 502 交回客户端。
3. **只做 HTTP 校验时，HTTPS 隧道的成功率会很低。** 打开 HTTPS 校验后，网关实际可用的比例会明显上升，刷新也更慢。
4. **重启后 health 为 0。** 第一轮刷新还没写完。HTTP 常要一两分钟，SOCKS5 和 HTTPS 校验更久。
5. **清理进程。** 用 `fuser -k -n tcp <port>`，或先 `ss -lptn` 再按 PID 结束。`pkill -f` 会扫到 SSH 会话的命令行。
6. **模型 API 走了代理。** 密钥会经过不受控的出口。用 `NO_PROXY` 把这些域名排除在外。

## 故障排查

| 现象 | 处理 |
|------|------|
| `401` | 请求里的 token 和单元 / 环境变量不一致 |
| `health` 数量为 0 | 先看 `last_refresh` 是否仍为 0，等待首轮结束 |
| 可用数量忽多忽少 | 公开代理本身会失效，等下一轮或 `GET /refresh` |
| 服务起不来 | `systemctl status proxy-pool` 和 `journalctl -u proxy-pool -e` |
| 网关 `502` | `GW_DEBUG=1` 看最后一个出口是 TCP 失败还是 CONNECT 被拒绝 |
| SOCKS5 始终为 0 | `python3 -c 'import socks'`，失败则安装 PySocks 或改用 `--no-socks5` |

## 许可

[MIT](LICENSE)。变更记录见 [CHANGELOG.md](CHANGELOG.md)。

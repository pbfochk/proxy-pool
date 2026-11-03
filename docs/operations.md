# 运维说明

面向把代理池装到自己的 Linux 服务器、再在笔记本上轮换出口的人。这里没有生产地址、口令或 token，示例一律用占位符。

## 拓扑

两条使用路径都依赖同一个池服务。

1. **池在服务器，轮换在笔记本。** `proxy_server.py` 监听 `32580`。笔记本上的 `local_rotate.sh` 周期性拉取列表，交给 mubeng，工具只访问 `127.0.0.1:10888`。
2. **池和轮换都在服务器。** 额外运行 `rotate_gateway.py`，只绑定 `127.0.0.1:7890`。人在笔记本上时，用 SSH 本地转发把这个端口带回来，而不是把网关改成公网监听。

```bash
ssh -N -L 7890:127.0.0.1:7890 user@YOUR_SERVER
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
export NO_PROXY=localhost,127.0.0.1,::1
export no_proxy="$NO_PROXY"
```

把会携带密钥的域名（模型 API、包源、镜像站）追加进 `NO_PROXY`。免费代理能看到经过它的请求内容。

## 安装与 token

```bash
sudo bash install.sh
```

脚本会：

- 安装 `python3`、`requests`，并尽量安装 `python3-socks` 或 PySocks。
- 把 `proxy-pool.service.example` 装到 `/etc/systemd/system/proxy-pool.service`。
- 若单元里仍是 `CHANGE_ME`，用 `secrets.token_hex(16)` 生成 token，写回单元，并写入 `/opt/proxy-pool/.env`（权限 `600`）。
- 把 `rotate_gateway.py` 和 `proxy-rotate.service` 装上，**不** `enable`。需要时再执行 `systemctl enable --now proxy-rotate`。

重复运行会沿用单元里已有的 token，终端不再打印它。token 只允许字母、数字和 `. _ ~ + -`，长度 8–128，这样写回单元文件时不会被 shell 或 sed 打断。

首次安装的终端输出和 shell 历史里会留下这份 token。记录到密码管理器之后清理历史。`journalctl -u proxy-pool` 的启动日志也会打出 token，因为进程需要让操作者能取回自动生成的值。限制谁能读 journal。

token 写在 `ExecStart` 里时，`ps` 和 `systemctl cat` 看得到。若要避开进程列表，删掉 `--token`，改为：

```
Environment=PROXY_POOL_TOKEN=...
```

未传 `--token` 时，`proxy_server.py` 和 `rotate_gateway.py` 都会读这个环境变量。

## 打开 HTTPS 校验

示例单元里的 `--https-target ""` 表示跳过隧道校验，和「先把服务跑起来」的部署方式一致。只做 HTTP 校验时，下面两类节点会留在池里：

- **假代理 / 透明代理。** TCP 能连，HTTP 也返回 200，但出口 IP 仍是服务器自己。`--anonymity` 看的是响应里有没有出现代理自己的 IP，盖不住这种情况。
- **企业出口拦截。** 例如 CONNECT 返回 403。网关对这种情况会换下一个出口；若池子里这种节点占多数，客户端会一直等到超时才收到 502。

把示例单元中的空值换成一个真实的 HTTPS 地址后再重启，例如：

```
--https-target https://example.com
```

HTTPS 校验只要求这一次请求没有抛错。现场经验是：补上之后，网关真正能用的比例会明显提高，刷新时间也会变长。

## SOCKS5

默认校验使用 `socks5h://`，DNS 在代理解析。示例单元额外带了 `--socks5-local-dns`，改成 `socks5://`，能通过校验的节点通常更多，DNS 查询发生在服务器本机。不能接受这一点时，从单元里删掉该参数。

系统没有 PySocks / `python3-socks` 时，SOCKS5 校验会失败。加上 `--no-socks5` 只保留 HTTP 池。

`rotate_gateway.py --type socks5` **不会**说 SOCKS 协议。它只是改从 SOCKS5 那份列表里取 `ip:port`，然后仍然发送 HTTP `CONNECT`。真正要轮换 SOCKS5 出口时，用笔记本上的：

```bash
PROTO=socks5 ./local_rotate.sh
```

mubeng 会按 `socks5://` 前缀去连。

## 进程与端口

重启后 `/health` 里两个计数为 0 是正常的。HTTP 校验常常要一两分钟，SOCKS5 更久，打开 HTTPS 校验之后更慢。等 `last_refresh` 变成非 0 再判断池子是不是空的。

清理监听时按端口或 PID 来，例如：

```bash
ss -lptn 'sport = :32580'
fuser -k -n tcp 32580
```

`pkill -f` 会匹配整条命令行，可能把当前 SSH 会话一起结束。`local_rotate.sh` 退出时只杀自己拉起的同步子进程。

## 排障

| 现象 | 处理 |
|------|------|
| API 401 | token 不一致。对照单元文件、`/opt/proxy-pool/.env` 和客户端环境变量 |
| health 长时间 0/0 | 首次刷新还没跑完，或校验目标不可达 |
| 网关大量 502 | 打开 `GW_DEBUG=1` 看是 TCP 失败还是 CONNECT 被拒绝；给池子加上 `--https-target` |
| 出口 IP 仍是服务器 | 池子里混进了假代理。打开 HTTPS 校验后重新刷新 |
| SOCKS5 池一直是 0 | 确认 `python3 -c 'import socks'` 成功，或暂时 `--no-socks5` |
| `local_rotate.sh` 立刻退出 | 看它打印的缺什么：`PROXY_POOL_TOKEN`、curl、awk 或 mubeng |
| 同步成功但 mubeng 没有出口 | 打开 `$HOME/.proxies/live.txt`，每行应带 `http://` 或 `socks5://` |

调试网关：

```bash
GW_DEBUG=1 python3 rotate_gateway.py --listen 127.0.0.1 --port 7890
# 或
python3 rotate_gateway.py --debug
```

每次失败的出口会打出来。最终放弃时，即使不开调试，也会留下一行 `CONNECT 失败` 或 `HTTP 转发失败`。

## 不进仓库的东西

- 填好的 `.env`、`/opt/proxy-pool/.env`
- `proxies.txt`、`socks5_proxies.txt`、mubeng 用的 `live.txt`
- `/etc/systemd/system/proxy-pool.service` 和 `proxy-rotate.service`（里面会有真实 token）
- SSH 私钥、主机口令、任何可登录的凭据

仓库里的单元文件必须保持 `*.service.example`，token 位置保持 `CHANGE_ME`。

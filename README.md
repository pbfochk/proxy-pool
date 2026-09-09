# Free Proxy Pool — 免费代理池服务

> 在服务器上常驻运行一个免费代理池服务：后台定时爬取并校验公开免费代理，通过 HTTP API 随时取用；本地再用 mubeng 跑一个自动轮换网关，测试工具指向 `127.0.0.1:10888` 即可自动换出口 IP。

- **用途**：渗透测试时绕过 IP 封禁 / 降低频率限制触发
- **定位**：免费代理练手方案；后期可平滑迁移到住宅代理（Bright Data / IPRoyal 等），架构不变
- **组成**：服务端（Python 常驻服务）+ 本地轮换网关（mubeng）

---

## 架构链路

```
你的工具 (Burp / sqlmap / nuclei / ffuf)
        ↓ 指向 127.0.0.1:10888
  mubeng 轮换网关 (本机 local_rotate.sh)
        ↓ 每 N 请求随机换一个
     免费代理 IP（来自代理池服务器）
        ↓
     目标系统
```

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `proxy_server.py` | 常驻服务版（线上实际部署） |
| `proxy_pool.py` | 单次运行版（练手 / 手动抓取） |
| `proxy-pool.service` | systemd 服务文件（含端口/token 配置） |
| `install.sh` | Linux 一键部署脚本 |
| `local_rotate.sh` | 本地 mubeng 自动轮换网关脚本 |

---

## 快速开始

### 1. 部署服务端（Linux 服务器）

```bash
# 把 proxy_server.py、proxy-pool.service、install.sh 上传到服务器，然后：
cd /tmp && bash install.sh
```

> 部署前先在 `proxy-pool.service` 的 `ExecStart` 行把 `YOUR_TOKEN_HERE` 换成你自己的 token。

### 2. 取用代理（API）

```bash
# 随机取一个可用代理
curl -s -H "X-Token: YOUR_TOKEN" http://YOUR_SERVER:32580/

# 全部可用代理
curl -s -H "X-Token: YOUR_TOKEN" http://YOUR_SERVER:32580/all

# 健康检查
curl -s -H "X-Token: YOUR_TOKEN" http://YOUR_SERVER:32580/health

# 手动刷新
curl -s -H "X-Token: YOUR_TOKEN" http://YOUR_SERVER:32580/refresh
```

### 3. 本地轮换网关（mubeng，推荐）

```bash
# 安装 mubeng
brew install mubeng        # macOS
# 或从 https://github.com/kitabisa/mubeng/releases 下载对应平台二进制

# 先改 local_rotate.sh 里的 TOKEN / SERVER，然后启动
./local_rotate.sh                          # 默认每 1 个请求换 IP
ROTATE_EVERY=10 ./local_rotate.sh          # 每 10 个请求换一次（更像真人）
LOCAL_PORT=9999 ./local_rotate.sh          # 换网关端口
```

启动后网关监听 `http://127.0.0.1:10888`，并定时从服务器同步最新代理（`-w` 自动热重载）。

### 4. 工具接入（全部指向本地网关）

```bash
# Burp：User options → Connections → Upstream Proxy Servers → Add
#       Destination host: *    Proxy host: 127.0.0.1   Port: 10888

sqlmap -u "http://目标" --proxy=http://127.0.0.1:10888
nuclei -u 目标 -proxy http://127.0.0.1:10888
ffuf -u "目标/FUZZ" -x http://127.0.0.1:10888 -w 字典
httpx -proxy http://127.0.0.1:10888 -u 目标
```

---

## API 端点一览（均需鉴权）

| 端点 | 方法 | 返回 |
|------|------|------|
| `/` | GET | 随机返回一个可用代理（纯文本 `ip:port`） |
| `/all` | GET | 返回全部可用代理（每行一个） |
| `/health` | GET | JSON：`{"alive": 数量, "last_refresh": 时间戳}` |
| `/stats` | GET | 同 `/health` |
| `/refresh` | GET | 手动触发一次刷新（异步，立即返回 `ok`） |

鉴权方式：`X-Token` 请求头，或 `?token=` query 参数。

---

## 服务端参数

```bash
python3 proxy_server.py \
  --port 32580 \                      # 监听端口
  --bind 0.0.0.0 \                    # 0.0.0.0=全网，127.0.0.1=仅本机
  --interval 300 \                    # 刷新间隔（秒）
  --target http://httpbin.org/ip \    # 校验用目标 URL
  --timeout 5 \                       # 读取超时（秒）
  --threads 300 \                     # 并发校验线程数
  --anonymity \                       # 只保留匿名/高匿代理
  --output proxies.txt \              # 持久化文件
  --token YOUR_TOKEN                  # 鉴权 token（留空则每次随机生成）
```

---

## 代码架构

```
main()
 ├─ 解析命令行参数 (argparse)
 ├─ 若无 --token，用 secrets 随机生成
 ├─ 创建 ProxyPool 对象
 ├─ pool.start_loop(interval) → 后台线程
 │     └─ 循环: refresh() → sleep(interval)
 └─ HTTPServer(0.0.0.0:port, handler)
       └─ serve_forever()

ProxyPool 类：
  - _fetch_sources(): 从 SOURCES 抓取免费代理，去重
  - _check(proxy): 通过代理访问 target 校验可用性（可选匿名性过滤）
  - refresh(): 抓取→合并历史→并发校验→更新内存池→写文件
  - random_proxy()/all_proxies()/stats(): 供 HTTP handler 调用，带线程锁
```

- **线程安全**：`pool` 用 `threading.Lock` 保护
- **连接超时优化**：`timeout=(3, self.timeout)`，死 IP 快速跳过
- **持久化**：刷新结果写 `proxies.txt`，重启后合并历史代理加速恢复
- **刷新防重入**：`refreshing` 标志防止 `/refresh` 并发重复刷新

---

## 代理源（SOURCES）

内置 6 个公开免费代理源：TheSpeedX、monosans、clarketm、proxifly、proxyscrape、proxy-list.download。改源时直接编辑 `proxy_server.py` 顶部的 `SOURCES` 列表。

---

## 本地网关参数

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `LOCAL_PORT` | `10888` | 本地网关端口 |
| `ROTATE_EVERY` | `1` | 每 N 个请求换一次 IP |
| `SYNC_INTERVAL` | `300` | 同步服务器代理的间隔（秒） |

`ROTATE_EVERY` 建议：扫描器爆破/目录扫描用 `1`；Burp 手动测试（会话型）用 `10`。

---

## 故障排查

| 现象 | 处理 |
|------|------|
| API 返回 401 | token 缺失或错误 |
| `health` 里 `alive: 0` | 首次刷新未完成（等 1~2 分钟），或目标站挂了 |
| 可用代理突然变少 | 免费代理质量波动，正常；`/refresh` 手动触发 |
| 服务起不来 | 查 `systemctl status proxy-pool` + `journalctl -xe` |
| 代理转发失败 | 免费代理已失效，换一个重试（免费代理常态） |

---

## 安全提醒

- **token 必须保密**：泄露则任何人都能白嫖你的代理池。
- 端口靠 token 鉴权保护；如需更严格，可启用 `ufw` 只放行 SSH 和 API 端口。
- 免费代理是公开共享的，质量有限；防护很强的目标建议升级住宅代理。

---

## 后续优化方向

1. 支持 SOCKS5 代理（当前仅 HTTP）
2. 按匿名度/协议/国家分类存储，API 加过滤参数
3. 对已入池代理做定期二次健康检查，自动剔除失效项
4. 迁移住宅代理：替换 `SOURCES` 抓取逻辑即可，架构不变
5. 加 Web 面板展示代理数量/质量趋势

---

> 仅用于**授权测试**，请遵守当地法律法规。

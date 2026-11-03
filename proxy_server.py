#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻代理池服务：后台定时刷新 HTTP + SOCKS5 双池，HTTP API 按 type 取用。

API (均需鉴权，默认 type=http):
  GET /health          返回双池统计 {"http_alive":N,"socks5_alive":N,"last_refresh":ts}
  GET /                随机返回一个可用代理 (纯文本 ip:port)
  GET /all             返回全部可用代理 (每行一个)
  GET /refresh         手动触发一次刷新
  通过 ?type=http 或 ?type=socks5 指定协议，例如:
    /?type=socks5      随机取一个 socks5 代理
    /all?type=socks5   取全部 socks5 代理

运行:
  python3 proxy_server.py --port 8888 --interval 300
"""

__version__ = "0.2.0"

import argparse
import json
import os
import random
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests

HTTP_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://www.proxy-list.download/api/v1/get?type=http",
]

SOCKS5_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://www.proxy-list.download/api/v1/get?type=socks5",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
]


def resolve_token(explicit):
    """命令行优先，其次 PROXY_POOL_TOKEN，最后现场生成。"""
    return explicit or os.environ.get("PROXY_POOL_TOKEN", "") or secrets.token_hex(16)


class ProxyPool:
    def __init__(self, target, timeout, threads, anonymity, output, https_target=None, enable_socks5=True, socks5_local_dns=False):
        self.target = target
        self.timeout = timeout
        self.threads = threads
        self.anonymity = anonymity
        self.http_output = output
        self.socks5_output = "socks5_" + output if not output.startswith("socks5_") else output
        self.https_target = https_target
        self.enable_socks5 = enable_socks5
        self.socks5_local_dns = socks5_local_dns
        self.lock = threading.Lock()
        self.http_pool = []
        self.socks5_pool = []
        self.last_refresh = 0
        self.refreshing = False

    def _fetch_sources(self, proto):
        sources = HTTP_SOURCES if proto == "http" else SOCKS5_SOURCES
        proxies = set()
        for url in sources:
            try:
                r = requests.get(url, timeout=15,
                                 headers={"User-Agent": random.choice(USER_AGENTS)})
                for line in r.text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and ":" in line:
                        proxies.add(line)
            except Exception:
                continue
        return proxies

    def _check(self, proxy, proto):
        if proto == "socks5":
            scheme = "socks5" if self.socks5_local_dns else "socks5h"
            p = {"http": f"{scheme}://{proxy}", "https": f"{scheme}://{proxy}"}
        else:
            p = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        try:
            r = requests.get(self.target, proxies=p, timeout=(3, self.timeout), headers=headers)
            if r.status_code != 200:
                return None
            if self.anonymity and proxy.split(":")[0] in r.text:
                return None
        except Exception:
            return None
        if self.https_target:
            try:
                requests.get(self.https_target, proxies=p, timeout=(3, self.timeout), headers=headers)
            except Exception:
                return None
        return proxy

    def _refresh_one(self, proto):
        output = self.http_output if proto == "http" else self.socks5_output
        raw = self._fetch_sources(proto)
        try:
            with open(output) as f:
                raw |= set(l.strip() for l in f if l.strip())
        except FileNotFoundError:
            pass

        alive = []
        with ThreadPoolExecutor(max_workers=self.threads) as ex:
            futs = [ex.submit(self._check, px, proto) for px in raw]
            for f in as_completed(futs):
                r = f.result()
                if r:
                    alive.append(r)

        alive = sorted(set(alive))
        with self.lock:
            if proto == "http":
                self.http_pool = alive
            else:
                self.socks5_pool = alive
            self.last_refresh = int(time.time())
        with open(output, "w") as f:
            f.write("\n".join(alive) + ("\n" if alive else ""))
        print(f"[*] {proto} 刷新完成: {len(alive)} 条可用代理", flush=True)

    def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            for proto in ("http", "socks5"):
                if proto == "socks5" and not self.enable_socks5:
                    continue
                try:
                    self._refresh_one(proto)
                except Exception as e:
                    print(f"[!] {proto} 刷新失败: {e}", flush=True)
        finally:
            self.refreshing = False

    def start_loop(self, interval):
        def loop():
            self.refresh()
            while True:
                time.sleep(interval)
                self.refresh()

        threading.Thread(target=loop, daemon=True).start()

    def _pool(self, proto):
        return self.http_pool if proto == "http" else self.socks5_pool

    def random_proxy(self, proto="http"):
        with self.lock:
            p = self._pool(proto)
            if p:
                return random.choice(p)
            return ""

    def all_proxies(self, proto="http"):
        with self.lock:
            return list(self._pool(proto))

    def stats(self):
        with self.lock:
            return {
                "http_alive": len(self.http_pool),
                "socks5_alive": len(self.socks5_pool),
                "last_refresh": self.last_refresh,
            }


def make_handler(pool, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _authed(self):
            if not token:
                return True
            if self.headers.get("X-Token") == token:
                return True
            qs = parse_qs(urlparse(self.path).query)
            return qs.get("token", [""])[0] == token

        def _send(self, body, ctype="text/plain", code=200):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._authed():
                self._send("unauthorized", code=401)
                return
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            ptype = qs.get("type", ["http"])[0]
            if ptype not in ("http", "socks5"):
                self._send("invalid type\n", code=400)
                return

            if path == "/health" or path == "/stats":
                self._send(json.dumps(pool.stats()), "application/json")
            elif path == "/all":
                data = pool.all_proxies(ptype)
                self._send("\n".join(data) + ("\n" if data else ""))
            elif path == "/refresh":
                threading.Thread(target=pool.refresh, daemon=True).start()
                self._send("ok\n")
            else:
                self._send(pool.random_proxy(ptype) + "\n")

    return Handler


def main():
    ap = argparse.ArgumentParser(description="常驻代理池服务")
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--interval", type=int, default=300, help="刷新间隔(秒)")
    ap.add_argument("--target", default="http://httpbin.org/ip", help="校验用测试 URL (HTTP)")
    ap.add_argument("--https-target", default="", help="校验 HTTPS 隧道能力的 URL，留空则跳过 HTTPS 校验")
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--threads", type=int, default=300)
    ap.add_argument("--anonymity", action="store_true")
    ap.add_argument("--output", default="proxies.txt")
    ap.add_argument("--token", default="", help="API 鉴权 token；留空则读 PROXY_POOL_TOKEN，仍空则自动生成")
    ap.add_argument("--no-socks5", action="store_true", help="禁用 SOCKS5 池（服务器无 PySocks 时用）")
    ap.add_argument("--socks5-local-dns", action="store_true", help="SOCKS5 用本地 DNS 解析(socks5://)，代理更多但有 DNS 泄漏；默认远程解析(socks5h://)")
    args = ap.parse_args()

    token = resolve_token(args.token)

    pool = ProxyPool(args.target, args.timeout, args.threads, args.anonymity, args.output,
                     https_target=args.https_target or None,
                     enable_socks5=not args.no_socks5,
                     socks5_local_dns=args.socks5_local_dns)
    pool.start_loop(args.interval)

    server = HTTPServer((args.bind, args.port), make_handler(pool, token))
    print(f"[*] proxy-pool {__version__}", flush=True)
    print(f"[*] 代理池服务启动: http://{args.bind}:{args.port}", flush=True)
    print(f"[*] 刷新间隔: {args.interval}s, 校验目标: {args.target}", flush=True)
    if args.https_target:
        print(f"[*] HTTPS 校验: {args.https_target}", flush=True)
    else:
        print("[*] HTTPS 校验未配置。需要过滤假代理时加上 --https-target。", flush=True)
    if args.no_socks5:
        print("[*] SOCKS5 池: 禁用", flush=True)
    else:
        dns_mode = "本地解析 socks5://" if args.socks5_local_dns else "远程解析 socks5h://"
        print(f"[*] SOCKS5 池: 启用（{dns_mode}）", flush=True)
    if args.bind in ("0.0.0.0", "::", "[::]"):
        print("[!] 代理池正在监听所有网卡。请使用长 token，并用防火墙限制来源。", flush=True)
    print(f"[*] API Token: {token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

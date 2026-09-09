#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻代理池服务：后台定时刷新 + HTTP API 随时取用。

API:
  GET /health          健康检查, 返回 {"alive": N, "last_refresh": ts}
  GET /                随机返回一个可用代理 (纯文本 ip:port)
  GET /all             返回全部可用代理 (每行一个)
  GET /refresh         手动触发一次刷新
  GET /stats           返回当前池子统计

运行:
  python3 proxy_server.py --port 8888 --interval 300
"""

import argparse
import json
import random
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests

SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://www.proxy-list.download/api/v1/get?type=http",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
]


class ProxyPool:
    def __init__(self, target, timeout, threads, anonymity, output):
        self.target = target
        self.timeout = timeout
        self.threads = threads
        self.anonymity = anonymity
        self.output = output
        self.lock = threading.Lock()
        self.pool = []
        self.last_refresh = 0
        self.refreshing = False

    def _fetch_sources(self):
        proxies = set()
        for url in SOURCES:
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

    def _check(self, proxy):
        p = {"http": f"http://{proxy}"}
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        try:
            r = requests.get(self.target, proxies=p, timeout=(3, self.timeout), headers=headers)
            if r.status_code != 200:
                return None
            if self.anonymity and proxy.split(":")[0] in r.text:
                return None
            return proxy
        except Exception:
            return None

    def refresh(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            raw = self._fetch_sources()
            try:
                with open(self.output) as f:
                    raw |= set(l.strip() for l in f if l.strip())
            except FileNotFoundError:
                pass

            alive = []
            with ThreadPoolExecutor(max_workers=self.threads) as ex:
                futs = [ex.submit(self._check, px) for px in raw]
                for f in as_completed(futs):
                    r = f.result()
                    if r:
                        alive.append(r)

            alive = sorted(set(alive))
            with self.lock:
                self.pool = alive
                self.last_refresh = int(time.time())
            with open(self.output, "w") as f:
                f.write("\n".join(alive) + ("\n" if alive else ""))
            print(f"[*] 刷新完成: {len(alive)} 条可用代理", flush=True)
        finally:
            self.refreshing = False

    def start_loop(self, interval):
        def loop():
            self.refresh()
            while True:
                time.sleep(interval)
                self.refresh()

        threading.Thread(target=loop, daemon=True).start()

    def random_proxy(self):
        with self.lock:
            if self.pool:
                return random.choice(self.pool)
            return ""

    def all_proxies(self):
        with self.lock:
            return list(self.pool)

    def stats(self):
        with self.lock:
            return {"alive": len(self.pool), "last_refresh": self.last_refresh}


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
            if self.path.split("?")[0] == "/health":
                self._send(json.dumps(pool.stats()), "application/json")
            elif self.path.split("?")[0] == "/stats":
                self._send(json.dumps(pool.stats()), "application/json")
            elif self.path.split("?")[0] == "/all":
                self._send("\n".join(pool.all_proxies()) + ("\n" if pool.all_proxies() else ""))
            elif self.path.split("?")[0] == "/refresh":
                threading.Thread(target=pool.refresh, daemon=True).start()
                self._send("ok\n")
            else:
                self._send(pool.random_proxy() + "\n")

    return Handler


def main():
    ap = argparse.ArgumentParser(description="常驻代理池服务")
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--interval", type=int, default=300, help="刷新间隔(秒)")
    ap.add_argument("--target", default="http://httpbin.org/ip", help="校验用测试 URL")
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--threads", type=int, default=300)
    ap.add_argument("--anonymity", action="store_true")
    ap.add_argument("--output", default="proxies.txt")
    ap.add_argument("--token", default="", help="API 鉴权 token，留空则自动生成")
    args = ap.parse_args()

    token = args.token or secrets.token_hex(16)

    pool = ProxyPool(args.target, args.timeout, args.threads, args.anonymity, args.output)
    pool.start_loop(args.interval)

    server = HTTPServer((args.bind, args.port), make_handler(pool, token))
    print(f"[*] 代理池服务启动: http://{args.bind}:{args.port}", flush=True)
    print(f"[*] 刷新间隔: {args.interval}s, 校验目标: {args.target}", flush=True)
    print(f"[*] API Token: {token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免费代理池：爬取公开代理源 -> 去重 -> 并发校验 -> 输出可用代理。

用法:
  python3 proxy_pool.py                          # 抓取并校验，输出 proxies.txt
  python3 proxy_pool.py --serve 8888             # 额外起一个轮换 API
  python3 proxy_pool.py --target https://www.baidu.com --min 50
  python3 proxy_pool.py --anonymity              # 只保留匿名/高匿代理

校验结果示例:
  curl http://127.0.0.1:8888/                    # 随机返回一个可用代理
  curl http://127.0.0.1:8888/all                 # 返回全部可用代理
"""

import argparse
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

# 免费公开代理源（HTTP/HTTPS）
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


def fetch_sources(timeout=15):
    proxies = set()
    for url in SOURCES:
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": random.choice(USER_AGENTS)})
            for line in r.text.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and ":" in line:
                    proxies.add(line)
        except Exception:
            continue
    return proxies


def check_proxy(proxy, target, timeout, anonymity, proto="http"):
    p = {proto: f"{proto}://{proxy}"}
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get(target, proxies=p, timeout=timeout, headers=headers)
        if r.status_code != 200:
            return None
        if anonymity:
            ip = r.text.strip()
            proxy_ip = proxy.split(":")[0]
            if proxy_ip in ip:
                return None
        return proxy
    except Exception:
        return None


def validate_all(proxies, target, timeout, anonymity, threads, proto="http"):
    alive = []
    with ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(check_proxy, px, target, timeout, anonymity, proto): px for px in proxies}
        for f in as_completed(futs):
            r = f.result()
            if r:
                alive.append(r)
                print(f"[+] 可用: {r}", flush=True)
    return alive


def load_existing(path):
    try:
        with open(path, "r") as f:
            return set(l.strip() for l in f if l.strip())
    except FileNotFoundError:
        return set()


def run_once(args):
    print("[*] 抓取公开代理源...")
    raw = fetch_sources()
    print(f"[*] 抓取到 {len(raw)} 条原始代理")

    raw |= load_existing(args.output)
    print(f"[*] 去重后共 {len(raw)} 条待校验")

    print(f"[*] 开始并发校验 (线程={args.threads}, 超时={args.timeout}s)...")
    alive = validate_all(raw, args.target, args.timeout, args.anonymity, args.threads)

    alive = sorted(set(alive))
    with open(args.output, "w") as f:
        f.write("\n".join(alive) + ("\n" if alive else ""))
    print(f"[*] 校验完成，{len(alive)} 条可用代理已写入 {args.output}")
    return alive


class PoolHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        pool = self.server.pool
        if self.path == "/all":
            body = "\n".join(pool).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            if pool:
                body = random.choice(pool).encode()
            else:
                body = b""
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def serve(pool, port):
    server = HTTPServer(("127.0.0.1", port), PoolHandler)
    server.pool = pool
    print(f"[*] 轮换 API 启动: http://127.0.0.1:{port}/ (随机)  /all (全部)")
    print("[!] 这个一次性 API 没有鉴权，只监听 127.0.0.1。", flush=True)
    server.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="免费代理池")
    ap.add_argument("--target", default="http://httpbin.org/ip", help="校验用的测试 URL")
    ap.add_argument("--timeout", type=int, default=8, help="单代理校验超时(秒)")
    ap.add_argument("--threads", type=int, default=200, help="并发校验线程数")
    ap.add_argument("--min", type=int, default=0, help="可用代理少于该数量时持续重抓")
    ap.add_argument("--anonymity", action="store_true", help="只保留匿名/高匿代理")
    ap.add_argument("--output", default="proxies.txt", help="输出文件")
    ap.add_argument("--serve", type=int, default=0, help="启动轮换 API 的端口")
    args = ap.parse_args()

    alive = run_once(args)
    while len(alive) < args.min:
        print(f"[!] 可用代理 {len(alive)} < {args.min}，60 秒后重抓...")
        time.sleep(60)
        alive = run_once(args)

    if args.serve:
        threading.Thread(target=serve, args=(alive, args.serve), daemon=True).start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()

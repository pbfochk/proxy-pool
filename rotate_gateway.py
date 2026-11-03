#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地轮换网关：把每个新 TCP 连接分配到代理池中的一个出口。

默认只监听 127.0.0.1。网关本身没有鉴权，不要改成 0.0.0.0。

用法:
    export PROXY_POOL_TOKEN=change-me
    python3 rotate_gateway.py --port 7890 --pool http://127.0.0.1:32580

然后给工具设置:
    export HTTP_PROXY=http://127.0.0.1:7890
    export HTTPS_PROXY=http://127.0.0.1:7890
    export NO_PROXY=localhost,127.0.0.1,::1
    curl -x http://127.0.0.1:7890 https://api.ipify.org

特性:
  - 纯标准库，无第三方依赖
  - 支持 CONNECT(HTTPS) 与普通 HTTP 绝对 URI 转发
  - 每次新连接换一个出口；CONNECT 隧道失败会自动重试下一个出口
  - --type 只决定从池子的哪一份列表取 ip:port，上行协议始终是 HTTP CONNECT
"""

__version__ = "0.2.0"

import argparse
import ipaddress
import os
import random
import select
import socket
import socketserver
import threading
import time
import traceback
import urllib.request

DEBUG = os.environ.get("GW_DEBUG", "") == "1"


def dbg(*a):
    if DEBUG:
        print(*a, flush=True)


def _is_loopback(host):
    name = host.strip().strip("[]").lower()
    if name in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def warn_if_public(listen):
    if _is_loopback(listen):
        return
    print(
        f"[!] 警告: 轮换网关正在监听 {listen}。它没有鉴权，只应绑定 127.0.0.1。",
        flush=True,
    )


def resolve_token(explicit):
    if explicit is None:
        return os.environ.get("PROXY_POOL_TOKEN", "")
    return explicit


def resolve_pool(explicit):
    if explicit:
        return explicit
    return os.environ.get("PROXY_POOL_SERVER", "") or "http://127.0.0.1:32580"


def build_parser():
    ap = argparse.ArgumentParser(description="本地轮换代理网关")
    ap.add_argument("--listen", default="127.0.0.1",
                    help="监听地址（默认 127.0.0.1，不要对公网开放）")
    ap.add_argument("--port", type=int, default=7890)
    ap.add_argument("--pool", default=None,
                    help="代理池 API 地址（省略时读 PROXY_POOL_SERVER，否则 http://127.0.0.1:32580）")
    ap.add_argument("--token", default=None,
                    help="代理池 API token（省略时读 PROXY_POOL_TOKEN）")
    ap.add_argument("--type", default="http", choices=["http", "socks5"],
                    help="从池中取哪一类 ip:port；网关与出口之间仍使用 HTTP CONNECT")
    ap.add_argument("--ttl", type=int, default=90, help="出口列表缓存秒数")
    ap.add_argument("--debug", action="store_true",
                    help="打印每次换出口的结果（等同 GW_DEBUG=1）")
    return ap


class UpstreamPool:
    def __init__(self, api, token, kind="http", ttl=90):
        self.api = api.rstrip("/")
        self.token = token
        self.kind = kind
        self.ttl = ttl
        self.items = []
        self.ts = 0.0
        self.lock = threading.Lock()

    def _fetch(self):
        url = f"{self.api}/all?type={self.kind}"
        headers = {"X-Token": self.token} if self.token else {}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode("utf-8", "ignore")
        except Exception as e:
            dbg(f"[pool] fetch fail: {e}")
            return []
        out = []
        for line in body.splitlines():
            line = line.strip()
            if line.count(":") == 1 and line.split(":")[1].isdigit():
                out.append(line)
        return out

    def load(self):
        with self.lock:
            fresh = self._fetch()
            if fresh:
                self.items = fresh
            self.ts = time.time()
            return len(self.items)

    def get(self):
        with self.lock:
            if not self.items or (time.time() - self.ts) > self.ttl:
                fresh = self._fetch()
                if fresh:
                    self.items = fresh
                self.ts = time.time()
            return random.choice(self.items) if self.items else None


def relay(a, b):
    socks = [a, b]
    try:
        while True:
            r, _, x = select.select(socks, [], socks, 60)
            if x:
                break
            if not r:
                continue
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except Exception:
        return


class Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def handle(self):
        try:
            line = self.rfile.readline(65536)
            if not line:
                return
            parts = line.decode("latin-1").strip().split()
            if len(parts) < 3:
                return
            method, target, ver = parts[0], parts[1], parts[2]

            headers = []
            while True:
                h = self.rfile.readline(65536)
                if not h or h in (b"\r\n", b"\n"):
                    break
                headers.append(h)

            if method.upper() == "CONNECT":
                self.do_connect(target)
            else:
                self.do_http(method, target, ver, headers)
        except Exception:
            if DEBUG:
                traceback.print_exc()

    def _open_tunnel(self, target, deadline=30):
        """反复换出口，直到某条代理成功建立 CONNECT 隧道。"""
        start = time.time()
        last = "no upstream"
        while time.time() - start < deadline:
            p = self.server.pool.get()
            if not p:
                return None, "pool empty"
            host, _, port = p.rpartition(":")
            try:
                s = socket.create_connection((host, int(port)), timeout=5)
            except Exception as e:
                last = f"{p} tcp: {e}"
                dbg(f"[tunnel] {last}")
                continue
            try:
                s.settimeout(8)
                s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
                buf = b""
                while b"\r\n\r\n" not in buf:
                    d = s.recv(4096)
                    if not d:
                        break
                    buf += d
                status = buf.split(b"\r\n", 1)[0] if buf else b""
                if b" 200" in status:
                    s.settimeout(None)
                    dbg(f"[tunnel] {p} -> {target} ok")
                    return s, None
                last = f"{p} reject: {status[:48]!r}"
                dbg(f"[tunnel] {last}")
                s.close()
            except Exception as e:
                last = f"{p} tunnel: {e}"
                dbg(f"[tunnel] {last}")
                try:
                    s.close()
                except Exception:
                    pass
        return None, last

    def do_connect(self, target):
        remote, err = self._open_tunnel(target)
        if not remote:
            print(f"[!] CONNECT 失败 {target}: {err}", flush=True)
            try:
                self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass
            return
        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            relay(self.connection, remote)
        finally:
            try:
                remote.close()
            except Exception:
                pass

    def do_http(self, method, target, ver, headers):
        start = time.time()
        last = "no upstream"
        while time.time() - start < 25:
            p = self.server.pool.get()
            if not p:
                break
            host, _, port = p.rpartition(":")
            try:
                remote = socket.create_connection((host, int(port)), timeout=5)
            except Exception as e:
                last = f"{p} tcp: {e}"
                dbg(f"[http] {last}")
                continue
            try:
                remote.sendall(f"{method} {target} {ver}\r\n".encode("latin-1"))
                for h in headers:
                    remote.sendall(h)
                remote.sendall(b"\r\n")
                remote.settimeout(None)
                dbg(f"[http] {p} -> {target}")
                relay(self.connection, remote)
                return
            except Exception as e:
                last = f"{p} relay: {e}"
                dbg(f"[http] {last}")
            finally:
                try:
                    remote.close()
                except Exception:
                    pass
        print(f"[!] HTTP 转发失败 {target}: {last}", flush=True)
        try:
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 256


def main():
    global DEBUG
    args = build_parser().parse_args()
    if args.debug or os.environ.get("GW_DEBUG", "") == "1":
        DEBUG = True

    token = resolve_token(args.token)
    pool_url = resolve_pool(args.pool)
    warn_if_public(args.listen)
    if args.type == "socks5":
        print(
            "[!] --type socks5 只切换出口列表。网关仍用 HTTP CONNECT 去连这些 ip:port。"
            "真正的 SOCKS5 出口请用 local_rotate.sh 的 PROTO=socks5（mubeng）。",
            flush=True,
        )

    pool = UpstreamPool(pool_url, token, args.type, args.ttl)
    print(f"[*] rotate-gateway {__version__}", flush=True)
    print(f"[*] 出口数量: {pool.load()}", flush=True)

    srv = Server((args.listen, args.port), Handler)
    srv.pool = pool
    print(f"[*] 轮换网关已启动: http://{args.listen}:{args.port}", flush=True)
    print(f"[*] 出口来源: {pool_url}/all?type={args.type}", flush=True)
    print(f"[*] 调试日志: {'开' if DEBUG else '关'}（GW_DEBUG=1 或 --debug）", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

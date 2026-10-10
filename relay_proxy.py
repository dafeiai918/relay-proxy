#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""relay-proxy · 中转站本地代理
藏密钥 / 局域网共享 / 模型改名 / 请求日志 / CORS，零依赖单文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__version__ = "1.0.0"

REMOTE_URL = "https://raw.githubusercontent.com/dafeiai918/oneclickrelay-remote/main/remote.json"
FALLBACK_RECOMMEND = {"name": "好同根", "url": "https://haotogen.com/"}

HOP_HEADERS = {"content-length", "transfer-encoding", "connection", "keep-alive",
               "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}

STATE = {
    "upstream": "",
    "real_key": "",
    "local_key": "",
    "model_map": {},
    "log_path": None,
    "start": time.time(),
    "count": 0,
    "tokens_in": 0,
    "tokens_out": 0,
}


class C:
    RESET = "\033[0m"; BOLD = "\033[1m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; CYAN = "\033[36m"; GRAY = "\033[90m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{C.RESET}"


def log_line(text: str) -> None:
    print(f"{_c(C.GRAY, time.strftime('%H:%M:%S'))} {text}", flush=True)


def write_log(record: dict) -> None:
    path = STATE["log_path"]
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"relay-proxy/{__version__}"

    def log_message(self, *args):
        pass

    # ---------- CORS ----------
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            self._health()
            return
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    # ---------- 本地状态页 ----------
    def _health(self):
        body = json.dumps({
            "ok": True,
            "version": __version__,
            "upstream": STATE["upstream"],
            "uptime_sec": round(time.time() - STATE["start"]),
            "requests": STATE["count"],
            "tokens_in": STATE["tokens_in"],
            "tokens_out": STATE["tokens_out"],
            "model_map": STATE["model_map"],
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ---------- 转发 ----------
    def _proxy(self, method: str):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else None
        model = ""
        if raw:
            try:
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict) and data.get("model") in STATE["model_map"]:
                    model = data["model"]
                    data["model"] = STATE["model_map"][model]
                    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
                elif isinstance(data, dict):
                    model = data.get("model", "")
            except Exception:
                pass

        target = STATE["upstream"].rstrip("/") + self.path
        headers = {
            "Authorization": f"Bearer {STATE['real_key']}",
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "Accept": self.headers.get("Accept", "application/json"),
            "User-Agent": f"relay-proxy/{__version__}",
        }
        for key in ("OpenAI-Organization", "OpenAI-Project"):
            if self.headers.get(key):
                headers[key] = self.headers[key]

        started = time.perf_counter()
        try:
            req = urllib.request.Request(target, data=raw, headers=headers, method=method)
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as exc:
            detail = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(detail)))
            self._cors()
            self.end_headers()
            self.wfile.write(detail)
            self._record(method, model, exc.code, started, 0, 0)
            return
        except Exception as exc:
            body = json.dumps({"error": {"message": f"上游不可达: {type(exc).__name__}",
                                         "type": "relay_proxy_error"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            self._record(method, model, 502, started, 0, 0)
            return

        ctype = resp.headers.get("Content-Type", "application/json")
        streaming = "text/event-stream" in ctype
        self.send_response(resp.status)
        self.send_header("Content-Type", ctype)
        self._cors()
        tokens_in = tokens_out = 0

        if streaming:
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for chunk in resp:
                    if not chunk:
                        continue
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            body = resp.read()
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
                usage = payload.get("usage") or {}
                tokens_in = usage.get("prompt_tokens", 0) or 0
                tokens_out = usage.get("completion_tokens", 0) or 0
            except Exception:
                pass
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        self._record(method, model, resp.status, started, tokens_in, tokens_out)

    def _record(self, method, model, status, started, tin, tout):
        dt = time.perf_counter() - started
        STATE["count"] += 1
        STATE["tokens_in"] += tin
        STATE["tokens_out"] += tout
        color = C.GREEN if status < 400 else C.RED
        mapped = f" -> {STATE['model_map'][model]}" if model in STATE["model_map"] else ""
        log_line(f"{_c(color, str(status))} {method} {self.path} "
                 f"{_c(C.GRAY, f'{model}{mapped} {dt:.2f}s')}"
                 + (f" {_c(C.CYAN, f'in={tin} out={tout}')}" if tin or tout else ""))
        write_log({"ts": time.time(), "ip": self.client_address[0], "method": method,
                   "path": self.path, "model": model, "status": status,
                   "seconds": round(dt, 3), "prompt_tokens": tin, "completion_tokens": tout})


# ---------------- 推荐位 ----------------

def fetch_recommend(timeout: float = 5.0) -> dict | None:
    try:
        req = urllib.request.Request(REMOTE_URL, headers={"User-Agent": f"relay-proxy/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        rec = data.get("recommend")
        if isinstance(rec, dict) and isinstance(rec.get("url"), str):
            return rec
    except Exception:
        pass
    return FALLBACK_RECOMMEND


def parse_map(text: str) -> dict:
    out = {}
    for pair in (text or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"模型映射格式应为 A=B，收到: {pair}")
        src, dst = pair.split("=", 1)
        out[src.strip()] = dst.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="relay-proxy", description="中转站本地代理：藏密钥 / 局域网共享 / 模型改名 / 日志")
    parser.add_argument("-u", "--upstream", help="上游地址，如 https://api.example.com/v1")
    parser.add_argument("-k", "--key", help="真实 API 密钥（或环境变量 RELAY_API_KEY）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，局域网共享用 0.0.0.0")
    parser.add_argument("-p", "--port", type=int, default=8788, help="监听端口，默认 8788")
    parser.add_argument("--local-key", default="local", help="客户端填的本地密钥，默认 local")
    parser.add_argument("-m", "--map", dest="model_map", default="",
                        help="模型映射，如 gpt-4=deepseek-chat,gpt-3.5-turbo=qwen-turbo")
    parser.add_argument("--log", help="请求日志写入 JSONL 文件")
    parser.add_argument("-V", "--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    upstream = args.upstream or os.environ.get("RELAY_BASE_URL", "")
    key = args.key or os.environ.get("RELAY_API_KEY", "")
    if not upstream:
        parser.error("缺少上游地址：用 -u 或环境变量 RELAY_BASE_URL")
    if not key:
        parser.error("缺少密钥：用 -k 或环境变量 RELAY_API_KEY")

    try:
        STATE["model_map"] = parse_map(args.model_map)
    except ValueError as exc:
        parser.error(str(exc))

    STATE.update({"upstream": upstream, "real_key": key,
                  "local_key": args.local_key, "log_path": args.log})

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown_host}:{args.port}/v1"

    print()
    print("═" * 60)
    print(f"  relay-proxy · 中转站本地代理  v{__version__}")
    print("═" * 60)
    print(f"  上游      {upstream}")
    print(f"  监听      {args.host}:{args.port}")
    if args.model_map:
        print(f"  模型映射  {STATE['model_map']}")
    print()
    print("  在各客户端里填：")
    print(f"    API 地址   {url}")
    print(f"    API 密钥   {args.local_key}      ← 客户端里填这个，真密钥不出本机")
    if args.host == "0.0.0.0":
        import socket
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            lan = probe.getsockname()[0]
            probe.close()
            print(f"\n  局域网其他设备用： http://{lan}:{args.port}/v1")
        except Exception:
            pass
    print("═" * 60)
    print("  Ctrl+C 停止")
    rec = fetch_recommend()
    if rec:
        print(f"  推荐中转站  {rec.get('name', '')}  {rec.get('url', '')}")
    print("═" * 60)
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print(f"  已停止。共处理 {STATE['count']} 个请求，"
              f"输入 {STATE['tokens_in']} / 输出 {STATE['tokens_out']} tokens")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

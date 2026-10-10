# -*- coding: utf-8 -*-
"""relay-proxy 测试：本地假上游 + 代理转发，全程不发外网请求"""
import json
import socket
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import relay_proxy as rp  # noqa: E402

SEEN = {}


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "mock-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        SEEN["auth"] = self.headers.get("Authorization")
        SEEN["model"] = payload.get("model")
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        cls.up_port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

        rp.STATE.update({
            "upstream": f"http://127.0.0.1:{cls.up_port}/v1",
            "real_key": "sk-real-secret",
            "local_key": "local",
            "model_map": {},
            "log_path": None,
            "count": 0,
        })
        rp.STATE["model_map"] = {}
        cls.proxy = ThreadingHTTPServer(("127.0.0.1", 0), rp.Handler)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.upstream.shutdown()

    def _post(self, path="/v1/chat/completions", payload=None):
        body = json.dumps(payload or {"model": "gpt-4o-mini",
                                      "messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.proxy_port}{path}", data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_health_endpoint(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.proxy_port}/health", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertIn("upstream", data)

    def test_real_key_is_injected(self):
        status, _ = self._post()
        self.assertEqual(status, 200)
        self.assertEqual(SEEN["auth"], "Bearer sk-real-secret")

    def test_local_key_never_forwarded(self):
        self._post()
        self.assertNotIn("local", SEEN["auth"])

    def test_usage_counted(self):
        before = rp.STATE["tokens_in"]
        self._post()
        self.assertGreater(rp.STATE["tokens_in"], before)

    def test_model_mapping(self):
        rp.STATE["model_map"] = {"gpt-4": "deepseek-chat"}
        try:
            self._post(payload={"model": "gpt-4", "messages": []})
            self.assertEqual(SEEN["model"], "deepseek-chat")
        finally:
            rp.STATE["model_map"] = {}

    def test_options_cors(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.proxy_port}/v1/chat/completions",
                                     method="OPTIONS")
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")

    def test_parse_map(self):
        self.assertEqual(rp.parse_map("a=b,c=d"), {"a": "b", "c": "d"})
        self.assertEqual(rp.parse_map(""), {})
        with self.assertRaises(ValueError):
            rp.parse_map("broken")


if __name__ == "__main__":
    unittest.main(verbosity=2)

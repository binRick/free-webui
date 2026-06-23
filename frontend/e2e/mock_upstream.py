#!/usr/bin/env python3
"""Deterministic OpenAI-compatible upstream for E2E tests — stdlib only.

Speaks just enough of the wire format for the real free-webui backend to drive
the UI without a live model:
  GET  /v1/models           -> a single model id "e2e-model"
  POST /v1/chat/completions -> SSE that echoes the last user message, so specs
                               can assert on deterministic reply text
  POST /v1/embeddings       -> deterministic toy vectors (RAG upload works)

Run: python3 e2e/mock_upstream.py [port]   (default 8910)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "e2e-model"


def _last_user_text(messages):
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            for part in c:
                if part.get("type") == "text":
                    return part.get("text", "")
        return ""
    return ""


def _chat_chunks(text):
    reply = f"You said: {text.strip()}" if text.strip() else "Hello from the mock model."
    # stream the reply in a few content deltas, then finish + a usage chunk
    head = {"choices": [{"delta": {"role": "assistant", "content": ""}, "index": 0}]}
    yield head
    words = reply.split(" ")
    for i, w in enumerate(words):
        piece = w if i == 0 else " " + w
        yield {"choices": [{"delta": {"content": piece}, "index": 0}]}
    yield {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}
    yield {
        "choices": [],
        "usage": {"prompt_tokens": 11, "completion_tokens": len(words), "total_tokens": 11 + len(words)},
    }


def _embedding(text):
    # never the zero vector, deterministic by length (mirrors the pytest stub)
    return [1.0 + (float(len(text) % 7) / 7.0)] * 8


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if self.path.rstrip("/").endswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self._json({"status": "ok"})

    def do_POST(self):
        body = self._body()
        if self.path.endswith("/chat/completions"):
            self._stream_chat(body)
        elif self.path.endswith("/embeddings"):
            inp = body.get("input")
            items = inp if isinstance(inp, list) else [inp or ""]
            self._json(
                {
                    "object": "list",
                    "model": body.get("model", "e2e-embed"),
                    "data": [
                        {"object": "embedding", "index": i, "embedding": _embedding(str(t))}
                        for i, t in enumerate(items)
                    ],
                }
            )
        else:
            self._json({"error": "not found"}, status=404)

    def _stream_chat(self, body):
        text = _last_user_text(body.get("messages"))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for chunk in _chat_chunks(text):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client (the backend) disconnected — fine


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8910
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock upstream on http://127.0.0.1:{port}/v1", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

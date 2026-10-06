"""Strict mock of Praktor's POST /api/chat for integration tests.

It mirrors the gateway's rules: Bearer chat token, 400 for an empty message,
integer-only timeout (Go rejects 120.0), chat_agents = [claude], and logs
every request so the test can check that nothing else was called.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = sys.argv[1]
ALLOWED = ["claude"]
KNOWN = {"claude", "deepseek"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        print(f"REQ GET {self.path}", flush=True)
        self._send(401, {"error": "unauthorized"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        print(f"REQ POST {self.path} {raw.decode(errors='replace')}", flush=True)
        if self.path != "/api/chat":
            return self._send(401, {"error": "unauthorized"})
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "unauthorized"})
        try:
            req = json.loads(raw)
        except ValueError:
            return self._send(400, {"error": "invalid request body"})
        if "timeout" in req and (
            not isinstance(req["timeout"], int) or isinstance(req["timeout"], bool)
        ):
            return self._send(400, {"error": "invalid request body"})
        if not str(req.get("message", "")).strip():
            return self._send(400, {"error": "message is required"})
        agent = req.get("agent") or ALLOWED[0]
        if agent not in KNOWN:
            return self._send(404, {"error": f"unknown agent: {agent}"})
        if agent not in ALLOWED:
            return self._send(
                403, {"error": f"agent not allowed for the chat token: {agent}"}
            )
        if req["message"] == "slow":
            return self._send(504, {"error": "timed out waiting for the agent's reply"})
        return self._send(200, {"agent": agent, "reply": f"pong: {req['message']}"})


# Test-only mock inside a throwaway Docker network.
ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104

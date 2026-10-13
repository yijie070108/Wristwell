"""用本地假服务器验证真实 SDK 发出的请求（不连网、不花钱）：参数名、beta 头、流式解析都要对。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import anthropic
import pytest

from wankangbao import report


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


def message_events(text, stop_reason="end_turn"):
    return [
        {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": {"output_tokens": 20}},
        {"type": "message_stop"},
    ]


@pytest.fixture
def fake_api():
    seen = []
    reply = {"events": message_events("## 报告正文")}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
            data = sse(reply["events"])
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = anthropic.Anthropic(api_key="test-key", base_url=f"http://127.0.0.1:{srv.server_port}", max_retries=0)
    yield client, seen, reply
    srv.shutdown()


def test_request_shape_and_text(fake_api, make_cfg):
    client, seen, _ = fake_api
    cfg = make_cfg()
    text = report.generate(cfg, "系统指令", "材料", client=client)
    assert text == "## 报告正文"
    req = seen[0]
    assert req["path"].startswith("/v1/messages")
    body = req["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["stream"] is True
    assert body["thinking"]["type"] == "adaptive"
    assert body["output_config"]["effort"] == cfg.effort == "max"
    assert body["max_tokens"] == report.MAX_TOKENS == 128000
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in req["headers"].get("anthropic-beta", "")


def test_refusal_raises(fake_api, make_cfg):
    client, _, reply = fake_api
    reply["events"] = message_events("", stop_reason="refusal")
    with pytest.raises(report.Refused):
        report.generate(make_cfg(), "系统指令", "材料", client=client)


def test_truncated_report_is_rejected(fake_api, make_cfg):
    client, _, reply = fake_api
    reply["events"] = message_events("## 报告写到一半", stop_reason="max_tokens")
    with pytest.raises(report.ReportError):
        report.generate(make_cfg(), "系统指令", "材料", client=client)


def test_no_fallbacks_for_unsupported_model(fake_api, make_cfg):
    client, seen, _ = fake_api
    report.generate(make_cfg(model="claude-haiku-5-5"), "系统指令", "材料", client=client)
    assert "fallbacks" not in seen[0]["body"]

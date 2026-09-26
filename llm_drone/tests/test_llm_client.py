import json

from llm.llm_client import OllamaLLMClient, TASK_JSON_SCHEMA


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self): return self.body


def test_ollama_uses_native_schema_temperature_zero_and_no_thinking(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        content = {
            "recognized": True, "type": "battery", "pad_id": None,
            "final_action": None, "reason": "查询电量",
        }
        return FakeResponse({"message": {"content": json.dumps(content)}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OllamaLLMClient("qwen3.5:9b", timeout_sec=12)
    result = client.parse("帮我看看还有多少电")
    payload = json.loads(captured["request"].data.decode())
    assert result["type"] == "battery"
    assert payload["format"] == TASK_JSON_SCHEMA
    assert payload["think"] is False
    assert payload["options"]["temperature"] == 0
    assert captured["timeout"] == 12


def test_ollama_health_check_requires_configured_model(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse({"models": [{"name": "another-model"}]}),
    )
    healthy, message = OllamaLLMClient("qwen3.5:9b").health_check()
    assert not healthy
    assert "not installed" in message

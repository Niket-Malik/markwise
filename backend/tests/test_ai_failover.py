import time
from unittest.mock import Mock

import httpx
import pytest

from backend.app import ai
from backend.app.main import Settings


@pytest.fixture(autouse=True)
def clean_circuits():
    ai.reset_provider_health_for_tests()
    yield
    ai.reset_provider_health_for_tests()


def test_http_errors_classify_retryable_and_permanent_without_leaking_body():
    for status in (429, 500, 503):
        response = Mock(spec=httpx.Response)
        response.status_code = status
        response.headers = {"Retry-After": "2"}
        response.text = '{"error":"private provider body"}'
        err = ai._http_error("gemini", response)
        assert err.retryable is (status != 429)
        if status == 429:
            assert err.scope == "provider_quota"
        assert err.retry_after == 2
        assert "private provider body" not in str(err)

    quota = Mock(spec=httpx.Response)
    quota.status_code = 403
    quota.headers = {}
    quota.text = '{"error":"quota exceeded"}'
    assert ai._http_error("gemini", quota).scope == "provider_quota"
    quota.text = '{"status":"RESOURCE_EXHAUSTED"}'
    assert ai._http_error("gemini", quota).scope == "provider_quota"

    invalid_key = Mock(spec=httpx.Response)
    invalid_key.status_code = 401
    invalid_key.headers = {}
    invalid_key.text = '{"error":"invalid API key"}'
    assert ai._http_error("gemini", invalid_key).retryable is False


def test_gemini_text_and_image_requests_preserve_schema_context(monkeypatch):
    captured = []
    payload = {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"findings": []}'}]}]}
    def fake_post(url, headers, json, timeout):
        captured.append((url, headers, json, timeout))
        response = Mock()
        response.status_code = 200
        response.is_error = False
        response.json.return_value = payload
        return response
    monkeypatch.setattr(ai.httpx, "post", fake_post)
    provider = ai.GeminiProvider(Settings(gemini_api_key="private-test", gemini_text_model="text-test",
                                          gemini_vision_model="vision-test"))
    source = Mock(id=8, kind="rule", title="Tone", body="Use clear wording", source="Guide", passage="Be clear", version=2)
    assert provider.analyse("Draft", {"goal": "inform"}, [source]).findings == []
    assert provider.analyse_image(b"pixels", "image/png", {"role": "logo"}, [source], b"reference").findings == []
    text_request, image_request = captured
    assert text_request[0].endswith("/v1beta/interactions")
    assert text_request[2]["model"] == "text-test"
    assert text_request[2]["store"] is False
    assert text_request[1]["x-goog-api-key"] == "private-test"
    assert text_request[2]["response_format"]["mime_type"] == "application/json"
    assert "approved_sources" in text_request[2]["input"][0]["text"]
    assert image_request[2]["model"] == "vision-test"
    parts = image_request[2]["input"]
    assert [part["data"] for part in parts[1:]] == ["cGl4ZWxz", "cmVmZXJlbmNl"]
    assert all(part["type"] == "image" for part in parts[1:])


def test_openai_alternate_requests_keep_strict_json_schema_for_text_and_vision(monkeypatch):
    calls = []
    config = Settings(ai_api_key="test-key", ai_model="gpt-6-luna", vision_model="gpt-6-luna")
    provider = ai.OpenAICompatibleProvider(config)
    def fake_post(endpoint, payload):
        calls.append((endpoint, payload))
        return {"choices": [{"message": {"content": '{"findings": []}'}}]}
    monkeypatch.setattr(provider, "_post", fake_post)
    provider.analyse("Synthetic draft", {}, [], model="gpt-5.6-luna")
    provider.analyse_image(b"image", "image/png", {}, [], model="gpt-5.6-luna")
    for endpoint, payload in calls:
        assert endpoint == "chat/completions"
        assert payload["model"] == "gpt-5.6-luna"
        format = payload["response_format"]
        assert format["type"] == "json_schema"
        assert format["json_schema"]["strict"] is True
        assert format["json_schema"]["schema"]["additionalProperties"] is False
    assert calls[1][1]["messages"][1]["content"][1]["type"] == "image_url"


@pytest.mark.parametrize("exception", [
    httpx.TimeoutException("secret text"),
    httpx.ConnectError("secret text"),
])
def test_timeout_and_connection_errors_are_retryable_and_safe(monkeypatch, exception):
    monkeypatch.setattr(ai.httpx, "post", Mock(side_effect=exception))
    with pytest.raises(ai.ProviderRequestError) as caught:
        ai._request("gemini", "https://example.invalid", headers={}, payload={}, timeout=1)
    assert caught.value.retryable
    assert "secret text" not in str(caught.value)


def test_transient_primary_failure_falls_back_and_opens_circuit(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="gemini-test", ai_fallback_api_key="openai-test",
                      embedding_model="text-embedding-3-small", ai_provider_max_retries=0,
                      ai_circuit_breaker_failures=1)

    class Primary:
        name = "gemini"
        capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None):
            raise ai.ProviderRequestError("gemini", "Gemini temporarily unavailable (HTTP 503)", True)

    class Fallback:
        name = "openai"
        capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna", "gpt-5.6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None): return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "GeminiProvider", Primary)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", Fallback)
    result = ai.FailoverProvider(config).analyse("draft", {}, [])
    assert result.findings == []
    assert ai.provider_status()["gemini"]["state"] == "open"
    assert ai.provider_status()["openai"]["state"] == "healthy"


def test_retries_are_bounded_exponential_jittered_and_honor_retry_after(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=2, ai_retry_base_seconds=1, ai_retry_max_seconds=3,
                      ai_circuit_breaker_failures=10)
    delays = []
    monkeypatch.setattr(ai.time, "sleep", delays.append)
    monkeypatch.setattr(ai.random, "uniform", lambda _low, _high: 1.0)

    class Unstable:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): self.calls = 0
        def analyse(self, *_, model=None):
            self.calls += 1
            raise ai.ProviderRequestError("gemini", "temporarily unavailable", True, retry_after=2)

    provider = Unstable(config)
    wrapper = ai.FailoverProvider(config)
    with pytest.raises(ai.ProviderRequestError):
        wrapper._attempt_provider_model(provider, "analyse", ("draft", {}, []), "gemini-3.8-flash")
    assert provider.calls == 3
    assert delays == [2, 2]


def test_bounded_provider_retry_can_recover_on_the_same_analysis_request(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=2, ai_retry_base_seconds=0, ai_retry_max_seconds=0,
                      ai_circuit_breaker_failures=10)
    monkeypatch.setattr(ai.time, "sleep", lambda _delay: None)

    class FlakyProvider:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): self.calls = 0
        def analyse(self, *_, model=None):
            self.calls += 1
            if self.calls == 1:
                raise ai.ProviderRequestError("gemini", "temporarily unavailable", True)
            return ai.AnalysisResponse(findings=[])

    provider = FlakyProvider(config)
    result = ai.FailoverProvider(config)._attempt_provider_model(provider, "analyse", ("draft", {}, []), "gemini-3.8-flash")
    assert result.findings == []
    assert provider.calls == 2


def test_primary_recovers_after_cooldown_and_becomes_active(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="gemini-test", ai_fallback_api_key="openai-test",
                      embedding_model="text-embedding-3-small", ai_provider_max_retries=0,
                      ai_circuit_breaker_failures=1, ai_circuit_breaker_cooldown_seconds=60)
    calls = {"primary": 0, "fallback": 0}

    class Primary:
        name = "gemini"
        capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None):
            calls["primary"] += 1
            if calls["primary"] == 1:
                raise ai.ProviderRequestError("gemini", "Gemini temporarily unavailable (HTTP 503)", True)
            return ai.AnalysisResponse(findings=[])

    class Fallback:
        name = "openai"
        capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None): calls["fallback"] += 1; return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "GeminiProvider", Primary)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", Fallback)
    provider = ai.FailoverProvider(config)
    provider.analyse("draft", {}, [])
    assert calls == {"primary": 1, "fallback": 1}
    ai._circuits["gemini"].open_until = time.monotonic() - 1
    provider.analyse("draft", {}, [])
    assert calls == {"primary": 2, "fallback": 1}
    assert ai.provider_status()["gemini"]["state"] == "healthy"


def test_permanent_primary_error_does_not_retry_or_fallback(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="bad-test", ai_fallback_api_key="openai-test",
                      ai_provider_max_retries=3)
    calls = {"primary": 0, "fallback": 0}

    class Primary:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None):
            calls["primary"] += 1
            raise ai.ProviderRequestError("gemini", "Gemini rejected the request (HTTP 401)", False)

    class Fallback:
        name = "openai"; capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None): calls["fallback"] += 1; return ai.AnalysisResponse()

    monkeypatch.setattr(ai, "GeminiProvider", Primary)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", Fallback)
    with pytest.raises(ai.AIProviderError, match="HTTP 401"):
        ai.FailoverProvider(config).analyse("draft", {}, [])
    assert calls == {"primary": 1, "fallback": 0}


def test_all_providers_down_returns_retryable_user_message(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=0, ai_circuit_breaker_failures=10)

    class Down:
        capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"] if self.name == "gemini" else ["gpt-6-luna", "gpt-5.6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None): raise ai.ProviderRequestError(self.name, "temporary failure", True)

    class Primary(Down): name = "gemini"
    class Fallback(Down): name = "openai"
    monkeypatch.setattr(ai, "GeminiProvider", Primary)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", Fallback)
    with pytest.raises(ai.AIProviderError, match="review was saved; please retry"):
        ai.FailoverProvider(config).analyse("draft", {}, [])


def test_vision_never_falls_back_to_text_only(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test", ai_provider_max_retries=0)
    class Primary:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash"]
        def supports_model(self, model, method): return True
        def analyse_image(self, *_, model=None): raise ai.ProviderRequestError("gemini", "temporary", True)
    class TextOnly:
        name = "openai"; capabilities = {"text"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-text-only"]
        def supports_model(self, model, method): return False
        def analyse_image(self, *_, model=None): pytest.fail("text-only provider must not receive images")
    monkeypatch.setattr(ai, "GeminiProvider", Primary)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", TextOnly)
    with pytest.raises(ai.AIProviderError):
        ai.FailoverProvider(config).analyse_image(b"image", "image/png", {}, [])


def test_gemini_primary_503_uses_gemini_alternate_before_openai(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=0, ai_circuit_breaker_failures=5)
    attempts = []

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            attempts.append(("gemini", model))
            if model == "gemini-3.8-flash":
                raise ai.ProviderRequestError("gemini", "temporary model error", True)
            return ai.AnalysisResponse(findings=[])

    class OpenAI:
        name = "openai"; capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna", "gpt-5.6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None): pytest.fail("provider fallback should not be needed")

    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", OpenAI)
    result = ai.FailoverProvider(config).analyse("draft", {}, [])
    assert result.findings == []
    assert attempts == [("gemini", "gemini-3.8-flash"), ("gemini", "gemini-3.7-flash")]


def test_gemini_provider_429_skips_alternate_and_switches_to_openai(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=2, ai_circuit_breaker_failures=5)
    attempts = []

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            attempts.append(("gemini", model))
            raise ai.ProviderRequestError("gemini", "quota exhausted", False, scope="provider_quota")

    class OpenAI:
        name = "openai"; capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna", "gpt-5.6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            attempts.append(("openai", model)); return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", OpenAI)
    ai.FailoverProvider(config).analyse("draft", {}, [])
    assert attempts == [("gemini", "gemini-3.8-flash"), ("openai", "gpt-6-luna")]


def test_openai_primary_503_uses_openai_alternate(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=0, ai_circuit_breaker_failures=5)
    attempts = []

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            attempts.append(("gemini", model))
            raise ai.ProviderRequestError("gemini", "provider quota", False, scope="provider_quota")

    class OpenAI:
        name = "openai"; capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna", "gpt-5.6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            attempts.append(("openai", model))
            if model == "gpt-6-luna": raise ai.ProviderRequestError("openai", "temporary model error", True)
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", OpenAI)
    assert ai.FailoverProvider(config).analyse("draft", {}, []).findings == []
    assert attempts == [("gemini", "gemini-3.8-flash"), ("openai", "gpt-6-luna"), ("openai", "gpt-5.6-luna")]


def test_openai_provider_429_skips_alternate_and_returns_retryable_error(monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=2, ai_circuit_breaker_failures=5)
    attempts = []

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            attempts.append(("gemini", model)); raise ai.ProviderRequestError("gemini", "quota", False, scope="provider_quota")

    class OpenAI:
        name = "openai"; capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna", "gpt-5.6-luna"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            attempts.append(("openai", model)); raise ai.ProviderRequestError("openai", "quota", False, scope="provider_quota")

    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", OpenAI)
    with pytest.raises(ai.AIProviderError, match="review was saved; please retry"):
        ai.FailoverProvider(config).analyse("draft", {}, [])
    assert attempts == [("gemini", "gemini-3.8-flash"), ("openai", "gpt-6-luna")]


def test_selected_models_are_vision_and_structured_output_capable_and_fail_closed():
    for model in ("gemini-3.8-flash", "gemini-3.7-flash"):
        assert ai.GeminiProvider.supports_model(model, "analyse_image")
    for model in ("gpt-6-luna", "gpt-5.6-luna"):
        assert ai.OpenAICompatibleProvider.supports_model(model, "analyse_image")
    assert not ai.OpenAICompatibleProvider.supports_model("gpt-text-only", "analyse_image")


@pytest.mark.parametrize(("gemini_behavior", "openai_behavior", "expected", "succeeds"), [
    ("temporary", "unused", [("gemini", "gemini-3.8-flash"), ("gemini", "gemini-3.7-flash")], True),
    ("quota", "success", [("gemini", "gemini-3.8-flash"), ("openai", "gpt-6-luna")], True),
    ("quota", "temporary", [("gemini", "gemini-3.8-flash"), ("openai", "gpt-6-luna"), ("openai", "gpt-5.6-luna")], True),
    ("quota", "quota", [("gemini", "gemini-3.8-flash"), ("openai", "gpt-6-luna")], False),
])
def test_mocked_vision_fallback_order(gemini_behavior, openai_behavior, expected, succeeds, monkeypatch):
    config = Settings(ai_provider="gemini", gemini_api_key="test", ai_fallback_api_key="test",
                      ai_provider_max_retries=0, ai_circuit_breaker_failures=10)
    attempts = []

    def failure(provider, behavior):
        if behavior == "quota":
            raise ai.ProviderRequestError(provider, "quota exhausted", False, scope="provider_quota")
        if behavior == "temporary":
            raise ai.ProviderRequestError(provider, "temporary model failure", True)

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse_image(self, *args, model=None):
            attempts.append(("gemini", model))
            behavior = gemini_behavior if model == "gemini-3.8-flash" else "success"
            failure("gemini", behavior)
            return ai.AnalysisResponse(findings=[])

    class OpenAI:
        name = "openai"; capabilities = {"text", "vision"}
        def __init__(self, *_args, **_kwargs): pass
        def models_for(self, method): return ["gpt-6-luna", "gpt-5.6-luna"]
        def supports_model(self, model, method): return True
        def analyse_image(self, *args, model=None):
            attempts.append(("openai", model))
            behavior = openai_behavior if model == "gpt-6-luna" else "success"
            failure("openai", behavior)
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    monkeypatch.setattr(ai, "OpenAICompatibleProvider", OpenAI)
    provider = ai.FailoverProvider(config)
    if succeeds:
        result = provider.analyse_image(b"synthetic-image", "image/png", {"role": "logo"}, [])
        assert result.findings == []
    else:
        with pytest.raises(ai.AIProviderError):
            provider.analyse_image(b"synthetic-image", "image/png", {"role": "logo"}, [])
    assert attempts == expected

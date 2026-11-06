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
        response.json.return_value = {"error": {"message": "private provider body"}}
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
    quota.json.return_value = {"error": {"message": "quota exceeded"}}
    assert ai._http_error("gemini", quota).scope == "provider_quota"
    quota.text = '{"status":"RESOURCE_EXHAUSTED"}'
    quota.json.return_value = {"status": "RESOURCE_EXHAUSTED"}
    assert ai._http_error("gemini", quota).scope == "provider_quota"

    invalid_key = Mock(spec=httpx.Response)
    invalid_key.status_code = 401
    invalid_key.headers = {}
    invalid_key.text = '{"error":"invalid API key"}'
    invalid_key.json.return_value = {"error": {"message": "invalid API key"}}
    assert ai._http_error("gemini", invalid_key).retryable is False


@pytest.mark.parametrize(("status", "code", "category"), [
    (400, "INVALID_ARGUMENT", "invalid_request"),
    (401, "UNAUTHENTICATED", "authentication_or_permission_rejected"),
    (403, "PERMISSION_DENIED", "authentication_or_permission_rejected"),
    (404, "NOT_FOUND", "model_or_endpoint_not_found"),
    (429, "RESOURCE_EXHAUSTED", "quota_or_rate_limit"),
    (503, "UNAVAILABLE", "provider_temporarily_unavailable"),
])
def test_provider_error_logs_diagnostic_fields_without_upstream_message_or_secrets(monkeypatch, caplog, status, code, category):
    response = Mock(spec=httpx.Response)
    response.status_code = status
    response.headers = {}
    response.is_error = True
    response.json.return_value = {"error": {
        "code": status, "status": code,
        "message": "private draft text and API key sk-secret-never-log",
    }}
    monkeypatch.setattr(ai.httpx, "post", Mock(return_value=response))
    with pytest.raises(ai.ProviderRequestError):
        ai._request("gemini", "https://example.invalid", headers={}, payload={}, timeout=1,
                    model="gemini-3.8-flash", operation="analyse")
    output = caplog.text
    assert "provider=gemini" in output
    assert "model=gemini-3.8-flash" in output
    assert "operation=analyse" in output
    assert f"status={status}" in output
    assert f"error_code={code}" in output
    assert f"error_message={category}" in output
    assert "private draft text" not in output
    assert "sk-secret-never-log" not in output


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
                                          gemini_vision_model="vision-test"), consent_guard=lambda: None)
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


def test_gemini_incomplete_interaction_logs_safe_failure_metadata(monkeypatch, caplog):
    def fake_post(_url, **_kwargs):
        response = Mock()
        response.status_code = 200
        response.is_error = False
        response.json.return_value = {"status": "failed", "error": {
            "code": "INTERNAL_ERROR", "message": "private submitted text"}}
        return response

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    provider = ai.GeminiProvider(Settings(gemini_api_key="not-logged", gemini_text_model="gemini-3.8-flash"), consent_guard=lambda: None)
    with pytest.raises(ai.ProviderRequestError):
        provider.analyse("private submitted text", {}, [])
    assert "model=gemini-3.8-flash" in caplog.text
    assert "operation=analyse" in caplog.text
    assert "status=failed" in caplog.text
    assert "error_code=INTERNAL_ERROR" in caplog.text
    assert "private submitted text" not in caplog.text
    assert "not-logged" not in caplog.text


def test_openai_alternate_requests_keep_strict_json_schema_for_text_and_vision(monkeypatch):
    calls = []
    config = Settings(ai_api_key="test-key", ai_model="gpt-6-luna", vision_model="gpt-6-luna")
    provider = ai.OpenAICompatibleProvider(config)
    def fake_post(endpoint, payload, **_kwargs):
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


def test_transient_openrouter_failure_falls_back_to_gemini_and_opens_circuit(monkeypatch):
    config = Settings(ai_provider="openrouter", openrouter_api_key="or-test", gemini_api_key="gemini-test",
                      ai_provider_max_retries=0, ai_circuit_breaker_failures=1)
    class Primary:
        name = "openrouter"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["nvidia/nemotron-3.5-super-vl:free"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None): raise ai.ProviderRequestError("openrouter", "temporary", True)
    class Fallback:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None): return ai.AnalysisResponse(findings=[])
    monkeypatch.setattr(ai, "OpenRouterProvider", Primary)
    monkeypatch.setattr(ai, "GeminiProvider", Fallback)
    result = ai.FailoverProvider(config, allow_openrouter=True).analyse("draft", {}, [])
    assert result.findings == []
    assert ai.provider_status()["openrouter"]["state"] == "open"
    assert ai.provider_status()["gemini"]["state"] == "healthy"

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


def test_openrouter_recovers_after_cooldown_and_becomes_active(monkeypatch):
    config = Settings(ai_provider="openrouter", openrouter_api_key="or-test", gemini_api_key="gemini-test",
                      ai_provider_max_retries=0, ai_circuit_breaker_failures=1, ai_circuit_breaker_cooldown_seconds=60)
    calls = {"primary": 0, "fallback": 0}
    class Primary:
        name = "openrouter"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["nvidia/nemotron-3.5-super-vl:free"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None):
            calls["primary"] += 1
            if calls["primary"] == 1: raise ai.ProviderRequestError("openrouter", "temporary", True)
            return ai.AnalysisResponse(findings=[])
    class Fallback:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None): calls["fallback"] += 1; return ai.AnalysisResponse(findings=[])
    monkeypatch.setattr(ai, "OpenRouterProvider", Primary)
    monkeypatch.setattr(ai, "GeminiProvider", Fallback)
    provider = ai.FailoverProvider(config, allow_openrouter=True)
    provider.analyse("draft", {}, [])
    assert calls == {"primary": 1, "fallback": 1}
    ai._circuits["openrouter"].open_until = time.monotonic() - 1
    provider.analyse("draft", {}, [])
    assert calls == {"primary": 2, "fallback": 1}
    assert ai.provider_status()["openrouter"]["state"] == "healthy"

def test_permanent_openrouter_error_does_not_retry_or_send_to_unconfigured_fallback(monkeypatch):
    config = Settings(ai_provider="openrouter", openrouter_api_key="bad-test", ai_provider_max_retries=3)
    calls = []
    class Primary:
        name = "openrouter"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["nvidia/nemotron-3.5-super-vl:free"]
        def supports_model(self, model, method): return True
        def analyse(self, *_, model=None):
            calls.append(model)
            raise ai.ProviderRequestError("openrouter", "rejected credentials", False, status_code=401)
    monkeypatch.setattr(ai, "OpenRouterProvider", Primary)
    with pytest.raises(ai.AIProviderError, match="temporarily unavailable"):
        ai.FailoverProvider(config, allow_openrouter=True).analyse("draft", {}, [])
    assert calls == ["nvidia/nemotron-3.5-super-vl:free"]

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


def test_gemini_primary_503_uses_gemini_alternate(monkeypatch):
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


def test_selected_models_are_vision_and_structured_output_capable_and_fail_closed():
    for model in ("gemini-3.8-flash", "gemini-3.7-flash"):
        assert ai.GeminiProvider.supports_model(model, "analyse_image")
    for model in ("gpt-6-luna", "gpt-5.6-luna"):
        assert ai.OpenAICompatibleProvider.supports_model(model, "analyse_image")
    assert not ai.OpenAICompatibleProvider.supports_model("gpt-text-only", "analyse_image")


def test_mocked_vision_provider_quota_falls_back_to_gemini(monkeypatch):
    config = Settings(ai_provider="openrouter", openrouter_api_key="or-test", gemini_api_key="gem-test",
                      ai_provider_max_retries=2, ai_circuit_breaker_failures=8)
    calls = []
    class OpenRouter:
        name = "openrouter"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["nvidia/nemotron-3.5-super-vl:free"]
        def supports_model(self, model, method): return True
        def analyse_image(self, *args, model=None):
            calls.append(("openrouter", model))
            raise ai.ProviderRequestError("openrouter", "quota", False, scope="provider_quota")
    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse_image(self, *args, model=None):
            calls.append(("gemini", model))
            return ai.AnalysisResponse(findings=[])
    monkeypatch.setattr(ai, "OpenRouterProvider", OpenRouter)
    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    result=ai.FailoverProvider(config, allow_openrouter=True).analyse_image(b"image", "image/png", {}, [])
    assert result.findings == []
    assert calls == [("openrouter", "nvidia/nemotron-3.5-super-vl:free"), ("gemini", "gemini-3.8-flash")]


def test_openrouter_text_uses_verified_model_and_strict_json_schema(monkeypatch):
    config = Settings(openrouter_api_key="test-key", openrouter_api_base_url="https://openrouter.example/api/v1")
    captured = {}
    monkeypatch.setattr(ai, "_request", lambda provider, url, **kwargs: (
        captured.update(provider=provider, url=url, **kwargs) or
        {"choices": [{"message": {"content": '{"findings": []}'}}]}
    ))
    result = ai.OpenRouterProvider(config, consent_guard=lambda: None).analyse("draft", {"goal": "inform"}, [])
    assert result.findings == []
    assert captured["provider"] == "openrouter"
    assert captured["url"] == "https://openrouter.example/api/v1/chat/completions"
    assert captured["payload"]["model"] == "nvidia/nemotron-3.5-super-vl:free"
    assert captured["payload"]["response_format"]["json_schema"]["strict"] is True
    assert ai.OpenRouterProvider.supports_model(captured["payload"]["model"], "analyse")


def test_openrouter_vision_uses_image_input_and_same_structured_schema(monkeypatch):
    config = Settings(openrouter_api_key="test-key")
    captured = {}
    monkeypatch.setattr(ai, "_request", lambda provider, url, **kwargs: (
        captured.update(provider=provider, url=url, **kwargs) or
        {"choices": [{"message": {"content": '{"findings": []}'}}]}
    ))
    result = ai.OpenRouterProvider(config, consent_guard=lambda: None).analyse_image(b"image", "image/png", {"role": "brand graphic"}, [])
    assert result.findings == []
    content = captured["payload"]["messages"][1]["content"]
    assert any(part["type"] == "image_url" for part in content)
    assert captured["payload"]["response_format"]["json_schema"]["strict"] is True
    assert ai.OpenRouterProvider.supports_model(captured["payload"]["model"], "analyse_image")
    assert not ai.OpenRouterProvider.supports_model("nvidia/nemotron-3-ultra-550b-a55b:free", "analyse_image")


def test_openrouter_embeddings_use_separate_query_passage_inputs_and_exact_1536(monkeypatch):
    config = Settings(openrouter_api_key="test-key", embedding_model="nvidia/nemotron-3.5-embed-1b:free", embedding_dimensions=1536)
    requests = []

    def request(provider, url, **kwargs):
        requests.append((provider, url, kwargs))
        vectors = [[float(index + 1) for index in range(1536)] for _ in kwargs["payload"]["input"]]
        return {"data": [{"index": i, "embedding": vector} for i, vector in enumerate(vectors)]}

    monkeypatch.setattr(ai, "_request", request)
    embedder = ai.OpenRouterEmbeddingProvider(config, consent_guard=lambda: None)
    query = embedder.embed_query("a search phrase")
    passage = embedder.embed_many(["source passage"])[0]
    assert len(query) == len(passage) == 1536
    assert sum(value * value for value in query) == pytest.approx(1)
    assert requests[0][1].endswith("/embeddings")
    assert requests[0][2]["payload"]["input_type"] == "query"
    assert requests[1][2]["payload"]["input_type"] == "passage"
    assert requests[0][2]["payload"]["model"] == "nvidia/nemotron-3.5-embed-1b:free"
    assert requests[0][2]["payload"]["dimensions"] == 1536


def test_openrouter_embedding_rejects_wrong_dimension_without_fallback(monkeypatch):
    config = Settings(openrouter_api_key="test-key", embedding_dimensions=1536)
    monkeypatch.setattr(ai, "_request", lambda *args, **kwargs: {"data": [{"index": 0, "embedding": [0.1] * 2048}]})
    with pytest.raises(ai.ProviderRequestError, match="invalid embedding"):
        ai.OpenRouterEmbeddingProvider(config, consent_guard=lambda: None).embed_query("query")
    with pytest.raises(ai.AIProviderError, match="1536"):
        ai.embedding_provider_from_config(Settings(openrouter_api_key="test-key", embedding_provider="openrouter", embedding_dimensions=2048), consent_guard=lambda: None)


def test_openrouter_quota_switches_to_gemini_without_openai_or_model_repeat(monkeypatch):
    config = Settings(ai_provider="openrouter", openrouter_api_key="or-key", gemini_api_key="gem-key",
                      ai_provider_max_retries=2, ai_circuit_breaker_failures=8)
    calls = []

    class OpenRouter:
        name = "openrouter"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["nvidia/nemotron-3.5-super-vl:free"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            calls.append(("openrouter", model))
            raise ai.ProviderRequestError("openrouter", "quota", False, scope="provider_quota")

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            calls.append(("gemini", model))
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "OpenRouterProvider", OpenRouter)
    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    result = ai.provider_from_config(config, allow_openrouter=True).analyse("draft", {}, [])
    assert result.findings == []
    assert calls == [("openrouter", "nvidia/nemotron-3.5-super-vl:free"), ("gemini", "gemini-3.8-flash")]


def test_openrouter_is_not_called_without_workspace_consent(monkeypatch):
    config = Settings(ai_provider="openrouter", openrouter_api_key="or-key", gemini_api_key="gem-key")
    calls = []

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash", "gemini-3.7-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            calls.append(("gemini", model))
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    provider = ai.provider_from_config(config, allow_openrouter=False)
    assert provider.analyse("draft", {}, []).findings == []
    assert calls == [("gemini", "gemini-3.8-flash")]


def test_query_embedding_cache_is_workspace_and_model_scoped_and_revocable(monkeypatch):
    config = Settings(openrouter_api_key="test-key", embedding_provider="openrouter", embedding_dimensions=1536)
    calls = []
    monkeypatch.setattr(ai, "_request", lambda provider, url, **kwargs: (
        calls.append((provider, kwargs["payload"]["input_type"])) or
        {"data": [{"index": 0, "embedding": [1.0] + [0.0] * 1535}]}
    ))
    embedder = ai.OpenRouterEmbeddingProvider(config, consent_guard=lambda: None)
    ai.clear_embedding_query_cache()
    embedder.embed_query("same query", workspace_id=101)
    embedder.embed_query("same query", workspace_id=101)
    embedder.embed_query("same query", workspace_id=202)
    assert calls == [("openrouter", "query"), ("openrouter", "query")]
    ai.clear_embedding_query_cache(101)
    embedder.embed_query("same query", workspace_id=101)
    assert len(calls) == 3
    ai.clear_embedding_query_cache()


def test_openrouter_global_rate_budget_counts_all_requests_and_expires(monkeypatch):
    config = Settings(openrouter_max_requests_per_minute=1)
    ai.reset_openrouter_rate_limiter_for_tests()
    clock = {"now": 100.0}
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock["now"])
    ai._reserve_openrouter_request(config)
    with pytest.raises(ai.ProviderRequestError) as caught:
        ai._reserve_openrouter_request(config)
    assert caught.value.scope == "provider_quota"
    assert caught.value.safe_message == "local_provider_rate_limit"
    clock["now"] += 61
    ai._reserve_openrouter_request(config)
    ai.reset_openrouter_rate_limiter_for_tests()


def test_consent_withdrawal_after_primary_failure_blocks_gemini_fallback(monkeypatch):
    state = {"external_version": 1, "openrouter_version": 1, "external_allowed": True,
             "openrouter_allowed": True}
    calls = []

    def check_openrouter():
        if (not state["external_allowed"] or not state["openrouter_allowed"]
                or state["external_version"] != 1 or state["openrouter_version"] != 1):
            raise ai.ConsentWithdrawnError("consent changed")

    def check_external():
        if not state["external_allowed"] or state["external_version"] != 1:
            raise ai.ConsentWithdrawnError("consent changed")

    config = Settings(ai_provider="openrouter", openrouter_api_key="or-key", gemini_api_key="gem-key",
                      ai_circuit_breaker_failures=8)

    class OpenRouter:
        name = "openrouter"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["nvidia/nemotron-3.5-super-vl:free"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            calls.append("openrouter")
            state["external_allowed"] = False
            state["openrouter_allowed"] = False
            state["external_version"] += 1
            state["openrouter_version"] += 1
            raise ai.ProviderRequestError("openrouter", "temporary failure", True)

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            calls.append("gemini")
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "OpenRouterProvider", OpenRouter)
    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    provider = ai.FailoverProvider(config, allow_openrouter=True,
                                   external_consent_guard=check_external,
                                   openrouter_consent_guard=check_openrouter)
    with pytest.raises(ai.ConsentWithdrawnError):
        provider.analyse("draft", {}, [])
    assert calls == ["openrouter"]


def test_openrouter_opt_out_allows_only_consented_gemini_fallback(monkeypatch):
    state = {"external": True, "openrouter": True, "external_version": 3, "openrouter_version": 7}
    calls = []

    def external_guard():
        if not state["external"] or state["external_version"] != 3:
            raise ai.ConsentWithdrawnError("external consent changed")

    def openrouter_guard():
        if (not state["external"] or not state["openrouter"] or state["external_version"] != 3
                or state["openrouter_version"] != 7):
            raise ai.ConsentWithdrawnError("OpenRouter consent changed")

    config = Settings(openrouter_api_key="or-key", gemini_api_key="gem-key", ai_provider="openrouter",
                      ai_provider_max_retries=1, ai_circuit_breaker_failures=8)

    class OpenRouter:
        name = "openrouter"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["nvidia/nemotron-3.5-super-vl:free"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            calls.append("openrouter")
            state["openrouter"] = False
            state["openrouter_version"] += 1
            raise ai.ProviderRequestError("openrouter", "temporarily unavailable", True)

    class Gemini:
        name = "gemini"; capabilities = {"text", "vision"}
        def __init__(self, _): pass
        def models_for(self, method): return ["gemini-3.8-flash"]
        def supports_model(self, model, method): return True
        def analyse(self, *args, model=None):
            calls.append("gemini")
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "OpenRouterProvider", OpenRouter)
    monkeypatch.setattr(ai, "GeminiProvider", Gemini)
    provider = ai.FailoverProvider(config, allow_openrouter=True,
                                   external_consent_guard=external_guard,
                                   openrouter_consent_guard=openrouter_guard)
    assert provider.analyse("draft", {}, []).findings == []
    assert calls == ["openrouter", "gemini"]


def test_embedding_response_after_consent_withdrawal_is_not_cached(monkeypatch):
    state = {"allowed": True}
    config = Settings(openrouter_api_key="test-key", embedding_dimensions=1536)

    def consent_guard():
        if not state["allowed"]:
            raise ai.ConsentWithdrawnError("consent changed")

    def request(*_args, **_kwargs):
        state["allowed"] = False
        return {"data": [{"index": 0, "embedding": [1.0] + [0.0] * 1535}]}

    monkeypatch.setattr(ai, "_request", request)
    ai.clear_embedding_query_cache()
    embedder = ai.OpenRouterEmbeddingProvider(config, consent_guard=consent_guard)
    with pytest.raises(ai.ConsentWithdrawnError):
        embedder.embed_query("revoke during request", workspace_id=501)
    assert not any(key[0].startswith("workspace:501:") for key in ai._query_embedding_cache)
    ai.clear_embedding_query_cache()


def test_legacy_openai_provider_is_only_used_when_explicitly_standalone():
    config = Settings(ai_provider="openai_compatible", ai_api_key="legacy-test",
                      openrouter_api_key="", gemini_api_key="")
    with pytest.raises(ai.AIProviderError, match="workspace external AI consent"):
        ai.provider_from_config(config)
    legacy = ai.provider_from_config(config, external_consent_guard=lambda: None)
    assert isinstance(legacy, ai.OpenAICompatibleProvider)

    preferred_config = Settings(ai_provider="openai_compatible", ai_api_key="legacy-test",
                                openrouter_api_key="or-test", gemini_api_key="gem-test")
    preferred = ai.provider_from_config(preferred_config, allow_openrouter=True,
                                       external_consent_guard=lambda: None,
                                       openrouter_consent_guard=lambda: None)
    assert isinstance(preferred, ai.FailoverProvider)
    assert [provider.name for provider in preferred.providers] == ["openrouter", "gemini"]

    configured_but_not_consented = Settings(ai_provider="openai_compatible", ai_api_key="legacy-test",
                                            openrouter_api_key="or-test", gemini_api_key="")
    with pytest.raises(ai.AIProviderError, match="No configured and consented AI provider"):
        ai.provider_from_config(configured_but_not_consented, external_consent_guard=lambda: None)

"""Consent-gated OpenRouter and Gemini analysis with tagged embeddings."""

from __future__ import annotations

import base64
import hashlib
from collections import OrderedDict, deque
import json
import logging
import math
import random
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from .main import Knowledge, Settings, settings

logger = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    """Safe, user-facing AI failure; never includes request data or credentials."""


class ConsentWithdrawnError(AIProviderError):
    """Raised when a workspace revokes consent while a request is in progress."""


class ProviderRequestError(AIProviderError):
    def __init__(self, provider: str, message: str, retryable: bool, retry_after: float | None = None,
                 scope: str = "model", *, status_code: int | None = None,
                 error_code: str | None = None, operation: str | None = None,
                 model: str | None = None, safe_message: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.retry_after = retry_after
        self.scope = scope
        self.status_code = status_code
        self.error_code = error_code
        self.operation = operation
        self.model = model
        self.safe_message = safe_message or message


class CandidateFinding(BaseModel):
    kind: Literal["claim", "tone", "terminology", "legal", "logo", "colour", "contrast", "accessibility", "other"]
    excerpt: str = Field(min_length=1, max_length=3000)
    explanation: str = Field(min_length=1, max_length=3000)
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1)
    cited_source_ids: list[int] = Field(default_factory=list)
    cannot_verify: bool = False
    suggested_rewrite: str | None = Field(default=None, max_length=3000)
    intent_preserved: bool = False


class AnalysisResponse(BaseModel):
    findings: list[CandidateFinding] = Field(default_factory=list, max_length=50)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, min(30.0, float(value)))
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, min(30.0, (when - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return None


def _http_error(provider: str, response: httpx.Response, *, model: str | None = None,
                operation: str | None = None) -> ProviderRequestError:
    status = response.status_code
    # Read only a machine-readable error code. Do not log arbitrary upstream messages,
    # which can echo submitted content or sensitive request values.
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            payload = {}
        error = payload.get("error", {})
        if not isinstance(error, dict):
            error = {"message": error}
        body = json.dumps(payload, ensure_ascii=True).lower()
        raw_code = error.get("status") or error.get("type") or error.get("code")
        error_code = re.sub(r"[^a-zA-Z0-9_.-]", "", str(raw_code))[:80] if raw_code is not None else None
    except Exception:
        body = ""
        error_code = None
    provider_quota = status in {402, 429} or (status == 403 and any(marker in body for marker in ("quota", "resource_exhausted", "insufficient_quota")))
    retryable = status in {408, 425} or 500 <= status <= 599
    if provider_quota:
        label, category = "quota/rate limit exhausted", "quota_or_rate_limit"
    elif status in {401, 403}:
        label, category = "rejected credentials or permissions", "authentication_or_permission_rejected"
    elif status == 404:
        label, category = "model or endpoint not found", "model_or_endpoint_not_found"
    elif status == 400:
        label, category = "rejected the request", "invalid_request"
    elif retryable:
        label, category = "temporarily unavailable", "provider_temporarily_unavailable"
    else:
        label, category = "rejected the request", "provider_rejected_request"
    return ProviderRequestError(
        provider, f"{provider} {label} (HTTP {status})", retryable,
        _parse_retry_after(response.headers.get("Retry-After")),
        scope="provider_quota" if provider_quota else "model", status_code=status,
        error_code=error_code, operation=operation, model=model, safe_message=category,
    )


def _request(provider: str, url: str, *, headers: dict, payload: dict, timeout: float,
             model: str | None = None, operation: str | None = None) -> dict:
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        if response.is_error is True or (isinstance(response.status_code, int) and response.status_code >= 400):
            error = _http_error(provider, response, model=model, operation=operation)
            logger.error("ai_provider_http_error provider=%s model=%s operation=%s status=%s error_code=%s error_message=%s",
                         provider, model or "unknown", operation or "unknown", error.status_code,
                         error.error_code or "unknown", error.safe_message)
            raise error
        result = response.json()
        if not isinstance(result, dict):
            raise ProviderRequestError(provider, f"{provider} returned an invalid response", False)
        return result
    except ProviderRequestError:
        raise
    except httpx.TimeoutException as exc:
        logger.error("ai_provider_transport_error provider=%s model=%s operation=%s status=none error_message=timeout",
                     provider, model or "unknown", operation or "unknown")
        raise ProviderRequestError(provider, f"{provider} request timed out", True) from exc
    except httpx.ConnectError as exc:
        logger.error("ai_provider_transport_error provider=%s model=%s operation=%s status=none error_message=connection_failed",
                     provider, model or "unknown", operation or "unknown")
        raise ProviderRequestError(provider, f"{provider} could not be reached", True) from exc
    except httpx.TransportError as exc:
        logger.error("ai_provider_transport_error provider=%s model=%s operation=%s status=none error_message=transport_failed",
                     provider, model or "unknown", operation or "unknown")
        raise ProviderRequestError(provider, f"{provider} connection failed", True) from exc
    except (ValueError, httpx.HTTPError) as exc:
        raise ProviderRequestError(provider, f"{provider} returned an invalid response", False) from exc


def _source_context(sources: list[Knowledge]) -> list[dict]:
    return [{
        "id": source.id, "type": source.kind, "title": source.title,
        "rule_or_fact": source.body, "source_document": source.source,
        "approved_passage": source.passage, "version": source.version,
    } for source in sources]


def _prompts(content: str, context: dict, sources: list[Knowledge], visual: bool = False) -> tuple[str, str]:
    schema = {
        "findings": [{
            "kind": "claim|tone|terminology|legal|logo|colour|contrast|accessibility|other",
            "excerpt": "exact substring copied from the draft or image region coordinates",
            "explanation": "plain-language reason grounded in approved source IDs",
            "severity": "low|medium|high|critical", "confidence": 0.0,
            "cited_source_ids": [1], "cannot_verify": False,
            "suggested_rewrite": "replacement for only the excerpt, or null", "intent_preserved": False,
        }]
    }
    if visual:
        system = (
            "Review the supplied image only against the provided approved workspace sources and declared intent. "
            "Never invent rules, source IDs, contrast measurements, or logo requirements. Each finding must cite an approved source ID. "
            "Use exact visible text or a region in the form 'Image region x=0 y=0 width=1 height=1'. "
            "Abstain on claims when evidence is insufficient. Preserve meaning in any suggested rewrite. Return JSON only matching: "
            + json.dumps(schema)
        )
        user = {"context": context, "approved_sources": _source_context(sources), "draft": content[:50000]}
    else:
        system = (
            "You are a pre-publication brand compliance analyst. The only authoritative guidance is the supplied approved-source list. "
            "Never invent a rule, fact, source, quotation, or citation. Every finding must quote an exact substring from the draft. "
            "For claims, cite relevant approved fact IDs only; if evidence is absent, contradictory, or insufficient, set cannot_verify=true, "
            "leave cited_source_ids empty, and do not guess. For rule findings, cite relevant approved rule IDs. Do not flag style or dialect "
            "unless an approved rule supports it. Suggested rewrites must change only the excerpt and preserve its intended meaning; set "
            "intent_preserved=true only when genuinely satisfied. Return JSON only matching: " + json.dumps(schema)
        )
        user = {"draft": content[:50000], "intent": context, "approved_sources": _source_context(sources)}
    return system, json.dumps(user, ensure_ascii=False)


class OpenAICompatibleProvider:
    """OpenAI Chat Completions adapter used for failover and legacy embeddings."""
    name = "openai"
    capabilities = frozenset({"text", "vision", "embeddings"})

    def __init__(self, config: Settings = settings, *, api_key: str | None = None, base_url: str | None = None,
                 text_model: str | None = None, vision_model: str | None = None, embedding_model: str | None = None,
                 consent_guard: Callable[[], None] | None = None):
        self.config = config
        self.api_key = config.ai_api_key if api_key is None else api_key
        self.base_url = (base_url or config.ai_api_base_url).rstrip("/")
        self.text_model = text_model if text_model is not None else config.ai_model
        self.vision_model = vision_model if vision_model is not None else (config.vision_model or self.text_model)
        self.embedding_model = embedding_model if embedding_model is not None else config.embedding_model
        self.consent_guard = consent_guard

    def models_for(self, method: str) -> list[str]:
        if method == "analyse_image":
            models = [self.config.ai_fallback_vision_model, self.config.ai_fallback_vision_model_alternate]
        else:
            models = [self.config.ai_fallback_text_model, self.config.ai_fallback_text_model_alternate]
        return list(dict.fromkeys(model.strip() for model in models if model and model.strip()))

    @staticmethod
    def supports_model(model: str, method: str) -> bool:
        # Fail closed for image/structured-output requests when an operator selects
        # an ID whose required capabilities have not been verified.
        supported = {"gpt-6-luna", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
                     "gpt-5.4-mini", "gpt-4o-mini"}
        vision = supported
        return model in (vision if method == "analyse_image" else supported)

    def _post(self, endpoint: str, payload: dict, *, model: str | None = None, operation: str | None = None) -> dict:
        if not self.api_key:
            raise ProviderRequestError(self.name, "OpenAI API key is not configured", False)
        if self.consent_guard:
            self.consent_guard()
        result = _request(self.name, f"{self.base_url}/{endpoint.lstrip('/')}",
                          headers={"Authorization": f"Bearer {self.api_key}"}, payload=payload,
                          timeout=self.config.ai_timeout_seconds, model=model or payload.get("model"),
                          operation=operation or endpoint)
        if self.consent_guard:
            self.consent_guard()
        return result

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not self.embedding_model:
            raise AIProviderError("EMBEDDING_MODEL is not configured")
        if not texts:
            return []
        result = self._post("embeddings", {"model": self.embedding_model, "input": [text[:8000] for text in texts]},
                            model=self.embedding_model, operation="embed_many")
        try:
            rows = sorted(result["data"], key=lambda row: int(row["index"]))
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
            if len(vectors) != len(texts) or any(len(vector) != 1536 or not all(math.isfinite(value) for value in vector) for vector in vectors):
                raise ValueError("Embedding dimensions, count, or values are invalid")
            return vectors
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderRequestError(self.name, "OpenAI returned an invalid embedding", False) from exc

    def analyse(self, content: str, context: dict, sources: list[Knowledge], *, model: str | None = None) -> AnalysisResponse:
        model = model or self.text_model
        if not model:
            raise ProviderRequestError(self.name, "OpenAI text model is not configured", False)
        system, user = _prompts(content, context, sources)
        result = self._post("chat/completions", {"model": model,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "markwise_analysis", "strict": True, "schema": GeminiProvider._response_schema(openai=True)}}, "messages": [
                {"role": "system", "content": system}, {"role": "user", "content": user}]},
                model=model, operation="analyse")
        return _parse_openai_result(result)

    def analyse_image(self, image: bytes, mime_type: str, context: dict, sources: list[Knowledge],
                      reference_logo: bytes | None = None, reference_mime_type: str = "image/png",
                      *, model: str | None = None) -> AnalysisResponse:
        model = model or self.vision_model
        if not model:
            raise ProviderRequestError(self.name, "OpenAI vision model is not configured", False)
        system, user = _prompts("", {**context, "has_reference_logo": reference_logo is not None}, sources, visual=True)
        content = [{"type": "text", "text": user}, {"type": "image_url", "image_url": {
            "url": f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"}}]
        if reference_logo:
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{reference_mime_type};base64,{base64.b64encode(reference_logo).decode('ascii')}"}})
        result = self._post("chat/completions", {"model": model,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "markwise_analysis", "strict": True, "schema": GeminiProvider._response_schema(openai=True)}}, "messages": [
                {"role": "system", "content": system}, {"role": "user", "content": content}]},
                model=model, operation="analyse_image")
        return _parse_openai_result(result)


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter's OpenAI-compatible chat endpoint for consented free models."""
    name = "openrouter"

    def __init__(self, config: Settings = settings, *, consent_guard: Callable[[], None] | None = None):
        self.consent_guard = consent_guard
        super().__init__(config, api_key=config.openrouter_api_key,
                         base_url=config.openrouter_api_base_url,
                         text_model=config.openrouter_text_model,
                         vision_model=config.openrouter_vision_model,
                         consent_guard=consent_guard)

    def models_for(self, method: str) -> list[str]:
        model = self.vision_model if method == "analyse_image" else self.text_model
        return [model.strip()] if model and model.strip() else []

    @staticmethod
    def supports_model(model: str, method: str) -> bool:
        # The selected Super VL free endpoint documents JSON schema and image input.
        return model == "nvidia/nemotron-3.5-super-vl:free"

    def _post(self, endpoint: str, payload: dict, *, model: str | None = None, operation: str | None = None) -> dict:
        if not self.api_key:
            raise ProviderRequestError(self.name, "OpenRouter API key is not configured", False)
        if self.consent_guard is None:
            raise ConsentWithdrawnError("OpenRouter workspace consent is required")
        self.consent_guard()
        _reserve_openrouter_request(self.config)
        self.consent_guard()
        result = _request(self.name, f"{self.base_url}/{endpoint.lstrip('/')}",
                          headers={"Authorization": f"Bearer {self.api_key}"}, payload=payload,
                          timeout=self.config.ai_timeout_seconds, model=model or payload.get("model"),
                          operation=operation or endpoint)
        self.consent_guard()
        return result


_openrouter_request_times: deque[float] = deque()
_openrouter_request_lock = threading.Lock()


def _reserve_openrouter_request(config: Settings = settings) -> None:
    """Conservatively cap all OpenRouter calls per process before contacting the provider."""
    now = time.monotonic()
    limit = max(1, min(60, config.openrouter_max_requests_per_minute))
    with _openrouter_request_lock:
        while _openrouter_request_times and now - _openrouter_request_times[0] >= 60:
            _openrouter_request_times.popleft()
        if len(_openrouter_request_times) >= limit:
            retry_after = max(1.0, 60 - (now - _openrouter_request_times[0]))
            raise ProviderRequestError("openrouter", "OpenRouter local request budget reached", False,
                                       retry_after=retry_after, scope="provider_quota",
                                       operation="rate_limit", safe_message="local_provider_rate_limit")
        _openrouter_request_times.append(now)


def reset_openrouter_rate_limiter_for_tests() -> None:
    with _openrouter_request_lock:
        _openrouter_request_times.clear()


_query_embedding_cache: OrderedDict[tuple[str, str], tuple[float, list[float]]] = OrderedDict()
_query_embedding_cache_lock = threading.Lock()
_QUERY_EMBEDDING_CACHE_TTL_SECONDS = 600
_QUERY_EMBEDDING_CACHE_MAX_ITEMS = 512


def clear_embedding_query_cache(workspace_id: int | str | None = None) -> None:
    """Forget cached vectors for a workspace when it revokes external AI consent."""
    with _query_embedding_cache_lock:
        if workspace_id is None:
            _query_embedding_cache.clear()
            return
        prefix = f"workspace:{workspace_id}:"
        for key in list(_query_embedding_cache):
            if key[0].startswith(prefix):
                _query_embedding_cache.pop(key, None)


class OpenRouterEmbeddingProvider:
    """Nemotron 3.5 Embed adapter with consistent 1536-D query/passage vectors."""
    name = "openrouter"

    def __init__(self, config: Settings = settings, *, consent_guard: Callable[[], None] | None = None):
        self.config = config
        self.consent_guard = consent_guard

    def _embed_many(self, texts: list[str], input_type: str) -> list[list[float]]:
        if not self.config.openrouter_api_key:
            raise ProviderRequestError(self.name, "OpenRouter API key is not configured", False)
        if input_type not in {"query", "passage"}:
            raise ValueError("Embedding input type must be query or passage")
        if not texts:
            return []
        if self.consent_guard is None:
            raise ConsentWithdrawnError("OpenRouter workspace consent is required")
        self.consent_guard()
        model = self.config.embedding_model
        dimensions = self.config.embedding_dimensions
        _reserve_openrouter_request(self.config)
        self.consent_guard()
        result = _request(self.name, f"{self.config.openrouter_api_base_url.rstrip('/')}/embeddings",
                          headers={"Authorization": f"Bearer {self.config.openrouter_api_key}"},
                          payload={"model": model, "input": [text[:30000] for text in texts],
                                   "dimensions": dimensions, "encoding_format": "float",
                                   "input_type": input_type}, timeout=self.config.ai_timeout_seconds,
                          model=model, operation="embed_query" if input_type == "query" else "embed_passage")
        self.consent_guard()
        try:
            rows = sorted(result["data"], key=lambda row: int(row["index"]))
            vectors = []
            for row in rows:
                vector = [float(value) for value in row["embedding"]]
                if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
                    raise ValueError("Unexpected embedding dimensions or values")
                norm = math.sqrt(sum(value * value for value in vector))
                if not math.isfinite(norm) or norm <= 0:
                    raise ValueError("Embedding has zero or invalid norm")
                # Truncation changes vector norm; re-normalize so cosine distance remains valid.
                vectors.append([value / norm for value in vector])
            if len(vectors) != len(texts) or dimensions != 1536:
                raise ValueError("Embedding response count or configured vector dimension is invalid")
            return vectors
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderRequestError(self.name, "OpenRouter returned an invalid embedding", False,
                                       model=model, operation="embed", safe_message="invalid_embedding_response") from exc

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return self._embed_many(texts, "passage")

    def embed_query(self, text: str, *, workspace_id: int | str | None = None) -> list[float]:
        if workspace_id is None:
            return self._embed_many([text], "query")[0]
        if self.consent_guard is None:
            raise ConsentWithdrawnError("OpenRouter workspace consent is required")
        self.consent_guard()
        space = embedding_space_for_config(self.config)
        scope = f"workspace:{workspace_id}:{space}"
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        key = (scope, fingerprint)
        now = time.monotonic()
        with _query_embedding_cache_lock:
            cached = _query_embedding_cache.get(key)
            if cached and now - cached[0] <= _QUERY_EMBEDDING_CACHE_TTL_SECONDS:
                _query_embedding_cache.move_to_end(key)
                return list(cached[1])
            if cached:
                _query_embedding_cache.pop(key, None)
        vector = self._embed_many([text], "query")[0]
        # Do not repopulate the query cache if consent was withdrawn in flight.
        self.consent_guard()
        with _query_embedding_cache_lock:
            _query_embedding_cache[key] = (time.monotonic(), list(vector))
            _query_embedding_cache.move_to_end(key)
            while len(_query_embedding_cache) > _QUERY_EMBEDDING_CACHE_MAX_ITEMS:
                _query_embedding_cache.popitem(last=False)
        return vector

    def embed(self, text: str) -> list[float]:
        # Compatibility alias is for indexing/document use; query paths call embed_query.
        return self.embed_many([text])[0]

def _parse_openai_result(result: dict) -> AnalysisResponse:
    try:
        raw = result["choices"][0]["message"]["content"]
        if isinstance(raw, list):
            raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
        return AnalysisResponse.model_validate_json(raw)
    except (KeyError, IndexError, TypeError, ValidationError) as exc:
        raise ProviderRequestError("openai", "OpenAI response did not match the finding schema", False) from exc


class GeminiProvider:
    name = "gemini"
    capabilities = frozenset({"text", "vision"})

    def __init__(self, config: Settings = settings, *, consent_guard: Callable[[], None] | None = None):
        self.config = config
        self.consent_guard = consent_guard

    def models_for(self, method: str) -> list[str]:
        if method == "analyse_image":
            models = [self.config.gemini_vision_model, self.config.gemini_vision_model_alternate]
        else:
            models = [self.config.gemini_text_model, self.config.gemini_text_model_alternate]
        return list(dict.fromkeys(model.strip() for model in models if model and model.strip()))

    @staticmethod
    def supports_model(model: str, method: str) -> bool:
        # Both selected Gemini models support text, image input and structured output.
        return model in {"gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"}

    @staticmethod
    def _response_schema(openai: bool = False) -> dict:
        nullable_rewrite = {"anyOf": [{"type": "string"}, {"type": "null"}]} if openai else {"type": "string", "nullable": True}
        return {
            "type": "object", "additionalProperties": False,
            "properties": {"findings": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["claim", "tone", "terminology", "legal", "logo", "colour", "contrast", "accessibility", "other"]},
                    "excerpt": {"type": "string"}, "explanation": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    "confidence": {"type": "number"}, "cited_source_ids": {"type": "array", "items": {"type": "integer"}},
                    "cannot_verify": {"type": "boolean"}, "suggested_rewrite": nullable_rewrite,
                    "intent_preserved": {"type": "boolean"},
                },
                "required": ["kind", "excerpt", "explanation", "severity", "confidence", "cited_source_ids", "cannot_verify", "suggested_rewrite", "intent_preserved"],
            }}},
            "required": ["findings"],
        }

    def _generate(self, model: str, system: str, user_text: str, image_parts: list[dict] | None = None) -> AnalysisResponse:
        if not self.config.gemini_api_key:
            raise ProviderRequestError(self.name, "Gemini API key is not configured", False)
        if self.consent_guard is None:
            raise ConsentWithdrawnError("External AI workspace consent is required")
        self.consent_guard()
        if not model:
            raise ProviderRequestError(self.name, "Gemini model is not configured", False)
        input_parts = [{"type": "text", "text": user_text}, *(image_parts or [])]
        payload = {
            "model": model,
            "input": input_parts,
            "system_instruction": system,
            "response_format": {"type": "text", "mime_type": "application/json", "schema": self._response_schema()},
            "store": False,
        }
        self.consent_guard()
        result = _request(self.name,
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={"x-goog-api-key": self.config.gemini_api_key}, payload=payload,
            timeout=self.config.ai_timeout_seconds,
            model=model, operation="analyse_image" if image_parts else "analyse")
        self.consent_guard()
        try:
            if result.get("status") not in (None, "completed"):
                response_error = result.get("error") if isinstance(result.get("error"), dict) else {}
                raw_code = response_error.get("status") or response_error.get("type") or response_error.get("code")
                error_code = re.sub(r"[^a-zA-Z0-9_.-]", "", str(raw_code))[:80] if raw_code is not None else None
                interaction_status = str(result.get("status", "unknown"))[:40]
                logger.error("ai_provider_incomplete_response provider=gemini model=%s operation=%s status=%s error_code=%s error_message=interaction_not_completed",
                             model, "analyse_image" if image_parts else "analyse", interaction_status,
                             error_code or "unknown")
                raise ProviderRequestError(self.name, "Gemini did not complete the analysis", False,
                                           status_code=200, error_code=error_code,
                                           operation="analyse_image" if image_parts else "analyse",
                                           model=model, safe_message="interaction_not_completed")
            raw = result.get("output_text")
            if not raw:
                raw = "".join(
                    part.get("text", "")
                    for step in result.get("steps", []) if step.get("type") == "model_output"
                    for part in step.get("content", []) if isinstance(part, dict) and part.get("type") == "text"
                )
            return AnalysisResponse.model_validate_json(raw)
        except ProviderRequestError:
            raise
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise ProviderRequestError(self.name, "Gemini response did not match the finding schema", False) from exc

    def analyse(self, content: str, context: dict, sources: list[Knowledge], *, model: str | None = None) -> AnalysisResponse:
        system, user = _prompts(content, context, sources)
        return self._generate(model or self.config.gemini_text_model, system, user)

    def analyse_image(self, image: bytes, mime_type: str, context: dict, sources: list[Knowledge],
                      reference_logo: bytes | None = None, reference_mime_type: str = "image/png",
                      *, model: str | None = None) -> AnalysisResponse:
        if not sources:
            return AnalysisResponse()
        system, user = _prompts("", {**context, "has_reference_logo": reference_logo is not None}, sources, visual=True)
        images = [{"type": "image", "mime_type": mime_type, "data": base64.b64encode(image).decode("ascii")}]
        if reference_logo:
            images.append({"type": "image", "mime_type": reference_mime_type,
                           "data": base64.b64encode(reference_logo).decode("ascii")})
        return self._generate(model or self.config.gemini_vision_model, system, user, images)


class _Circuit:
    def __init__(self):
        self.failures = 0
        self.open_until = 0.0
        self.last_error: str | None = None
        self.last_success: float | None = None
        self.state = "healthy"
        self.half_open_inflight = False


_circuits: dict[str, _Circuit] = {"openrouter": _Circuit(), "gemini": _Circuit(), "openai": _Circuit()}
_circuit_lock = threading.Lock()


class FailoverProvider:
    """Consent-gated OpenRouter first, Gemini secondary; bounded retries and recovery."""
    def __init__(self, config: Settings = settings, *, allow_openrouter: bool = False,
                 external_consent_guard: Callable[[], None] | None = None,
                 openrouter_consent_guard: Callable[[], None] | None = None):
        self.config = config
        self.providers = []
        if allow_openrouter and config.openrouter_api_key:
            primary = OpenRouterProvider(config)
            primary.consent_guard = openrouter_consent_guard
            self.providers.append(primary)
        if config.gemini_api_key:
            fallback = GeminiProvider(config)
            fallback.consent_guard = external_consent_guard
            self.providers.append(fallback)
        self.primary = self.providers[0] if self.providers else None
        self.fallback = self.providers[1] if len(self.providers) > 1 else None

    @staticmethod
    def _available(name: str) -> bool:
        with _circuit_lock:
            circuit = _circuits[name]
            if circuit.open_until and time.monotonic() < circuit.open_until:
                return False
            if circuit.open_until:
                # Half-open: exactly one operation probes recovery under lock.
                circuit.state = "half_open"
                circuit.open_until = 0
                circuit.half_open_inflight = True
            elif circuit.state == "half_open" and circuit.half_open_inflight:
                return False
            return True

    def _record(self, name: str, success: bool, error: Exception | None = None):
        with _circuit_lock:
            circuit = _circuits[name]
            if success:
                circuit.failures = 0
                circuit.open_until = 0
                circuit.last_error = None
                circuit.last_success = time.time()
                circuit.state = "healthy"
                circuit.half_open_inflight = False
            else:
                circuit.half_open_inflight = False
                circuit.failures += 1
                circuit.last_error = type(error).__name__ if error else "ProviderError"
                if circuit.failures >= max(1, self.config.ai_circuit_breaker_failures):
                    circuit.open_until = time.monotonic() + max(1, self.config.ai_circuit_breaker_cooldown_seconds)
                    circuit.state = "open"
                else:
                    circuit.state = "degraded"

    def _attempt_provider_model(self, provider, method: str, args: tuple, model: str):
        name = provider.name
        max_retries = max(0, min(5, self.config.ai_provider_max_retries))
        for attempt in range(max_retries + 1):
            try:
                if provider.name == "openrouter" and getattr(provider, "consent_guard", None):
                    provider.consent_guard()
                elif provider.name == "gemini" and getattr(provider, "consent_guard", None):
                    provider.consent_guard()
                result = getattr(provider, method)(*args, model=model)
                consent_guard = getattr(provider, "consent_guard", None)
                if consent_guard:
                    consent_guard()
                self._record(name, True)
                return result
            except ProviderRequestError as exc:
                self._record(name, False, exc)
                # Quota/rate limits are shared at the provider/project level.
                # Do not spend time switching models within this provider.
                if exc.scope == "provider_quota":
                    logger.warning("ai_provider_quota provider=%s operation=%s model=%s", name, method, model)
                    raise
                if not exc.retryable or attempt >= max_retries:
                    raise
                max_delay = max(0.0, self.config.ai_retry_max_seconds)
                exponential = min(max_delay, max(0.0, self.config.ai_retry_base_seconds) * (2 ** attempt))
                wait = min(max_delay, max(exponential * random.uniform(0.5, 1.5), exc.retry_after or 0))
                logger.warning("ai_provider_retry provider=%s operation=%s model=%s attempt=%s delay_seconds=%.2f", name, method, model, attempt + 1, wait)
                time.sleep(wait)
            except ConsentWithdrawnError:
                raise
            except AIProviderError as exc:
                if isinstance(exc, ConsentWithdrawnError):
                    raise
                self._record(name, False, exc)
                raise ProviderRequestError(name, str(exc), False) from exc

    def _call_provider_models(self, provider, method: str, args: tuple):
        models = provider.models_for(method)
        capable_models = [model for model in models if provider.supports_model(model, method)]
        if not capable_models:
            raise ProviderRequestError(provider.name, f"{provider.name} has no configured model verified for {method}", False)
        last_error = None
        for index, model in enumerate(capable_models):
            try:
                result = self._attempt_provider_model(provider, method, args, model)
                if index:
                    logger.info("ai_model_fallback provider=%s operation=%s model=%s", provider.name, method, model)
                return result
            except ProviderRequestError as exc:
                last_error = exc
                if exc.scope == "provider_quota" or not exc.retryable:
                    raise
                if index + 1 < len(capable_models):
                    logger.warning("ai_model_failover provider=%s from_model=%s to_model=%s operation=%s", provider.name, model, capable_models[index + 1], method)
        raise last_error or ProviderRequestError(provider.name, f"{provider.name} models unavailable", True)

    def _call(self, method: str, *args):
        if not self.providers:
            raise AIProviderError("Configure an opted-in AI provider to enable analysis")
        required_capability = "vision" if method == "analyse_image" else "text"
        attempted = False
        for provider in self.providers:
            if required_capability not in provider.capabilities:
                continue
            if not self._available(provider.name):
                logger.info("ai_provider_circuit_open provider=%s operation=%s", provider.name, method)
                continue
            attempted = True
            try:
                if provider.name == "openrouter" and getattr(provider, "consent_guard", None):
                    provider.consent_guard()
                elif provider.name == "gemini" and getattr(provider, "consent_guard", None):
                    provider.consent_guard()
                result = self._call_provider_models(provider, method, args)
                logger.info("ai_provider_success provider=%s operation=%s", provider.name, method)
                return result
            except ConsentWithdrawnError:
                # Losing OpenRouter-specific opt-in removes only that provider;
                # Gemini may still run only if its separate external-AI guard passes.
                if provider.name == "openrouter":
                    logger.info("ai_provider_consent_revoked provider=openrouter operation=%s", method)
                    continue
                raise
            except ProviderRequestError as exc:
                logger.error("ai_provider_failure provider=%s model=%s operation=%s status=%s error_code=%s error_message=%s retryable=%s",
                             provider.name, exc.model or "unknown", method, exc.status_code or "none",
                             exc.error_code or "unknown", exc.safe_message, exc.retryable)
                if provider is not self.providers[-1]:
                    next_provider = next((p for p in self.providers[self.providers.index(provider) + 1:]
                                          if required_capability in p.capabilities), None)
                    if next_provider:
                        logger.warning("ai_provider_failover from=%s to=%s operation=%s", provider.name, next_provider.name, method)
            except AIProviderError as exc:
                self._record(provider.name, False, exc)
                logger.error("ai_provider_failure provider=%s model=unknown operation=%s status=none error_code=unknown error_message=provider_error",
                             provider.name, method)
        if not attempted:
            logger.error("ai_provider_unavailable operation=%s", method)
        raise AIProviderError("AI analysis is temporarily unavailable. Your review was saved; please retry the analysis shortly.")

    def analyse(self, content: str, context: dict, sources: list[Knowledge]) -> AnalysisResponse:
        return self._call("analyse", content, context, sources)

    def analyse_image(self, image: bytes, mime_type: str, context: dict, sources: list[Knowledge],
                      reference_logo: bytes | None = None, reference_mime_type: str = "image/png") -> AnalysisResponse:
        # Each selected provider explicitly advertises vision capability; do not
        # call a text-only model if this changes in a future configuration.
        return self._call("analyse_image", image, mime_type, context, sources, reference_logo, reference_mime_type)


def provider_status() -> dict:
    with _circuit_lock:
        return {name: {"state": item.state, "consecutive_failures": item.failures,
                       "last_error_type": item.last_error, "last_success_at": item.last_success}
                for name, item in _circuits.items()}


def reset_provider_health_for_tests():
    reset_openrouter_rate_limiter_for_tests()
    with _circuit_lock:
        for item in _circuits.values():
            item.failures = 0; item.open_until = 0; item.last_error = None; item.last_success = None; item.state = "healthy"; item.half_open_inflight = False


def provider_from_config(config: Settings = settings, *, allow_openrouter: bool = False,
                         external_consent_guard: Callable[[], None] | None = None,
                         openrouter_consent_guard: Callable[[], None] | None = None):
    provider = config.ai_provider.strip().lower()
    if provider in {"openai", "openai_compatible"} and config.ai_api_key:
        logger.warning("Legacy AI_PROVIDER=%s is ignored when OpenRouter or Gemini is configured", provider)
    if allow_openrouter and config.openrouter_api_key:
        return FailoverProvider(config, allow_openrouter=True,
                                external_consent_guard=external_consent_guard,
                                openrouter_consent_guard=openrouter_consent_guard)
    if config.gemini_api_key:
        return FailoverProvider(config, allow_openrouter=False,
                                external_consent_guard=external_consent_guard)
    if (provider in {"openai", "openai_compatible"} and config.ai_api_key
            and not config.openrouter_api_key and not config.gemini_api_key):
        if external_consent_guard is None:
            raise AIProviderError("Legacy OpenAI-compatible provider requires workspace external AI consent")
        logger.warning("Using explicitly configured legacy AI_PROVIDER=%s; OpenRouter/Gemini remain preferred when configured", provider)
        return OpenAICompatibleProvider(config, consent_guard=external_consent_guard)
    if provider == "openrouter" and not allow_openrouter:
        raise AIProviderError("Enable the OpenRouter free-provider privacy option before using this provider")
    raise AIProviderError("No configured and consented AI provider is available")


def embedding_space_for_config(config: Settings = settings) -> str:
    return f"{config.embedding_provider}:{config.embedding_model}:{config.embedding_dimensions}:query-passage-v1"


def embedding_provider_from_config(config: Settings = settings, *,
                                   consent_guard: Callable[[], None] | None = None) -> OpenRouterEmbeddingProvider:
    """OpenRouter is the sole embedding provider; callers gate it on workspace consent."""
    if config.embedding_provider.strip().lower() != "openrouter":
        raise AIProviderError("Configure EMBEDDING_PROVIDER=openrouter; paid OpenAI embeddings are disabled")
    if not config.openrouter_api_key or not config.embedding_model or config.embedding_dimensions != 1536:
        raise AIProviderError("OpenRouter embedding configuration is incomplete or incompatible with vector(1536)")
    if consent_guard is None:
        raise ConsentWithdrawnError("OpenRouter workspace consent is required")
    return OpenRouterEmbeddingProvider(config, consent_guard=consent_guard)

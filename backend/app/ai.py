"""OpenAI-compatible provider adapter with validated, structured analysis output."""

from __future__ import annotations

import json
import logging
import math
import base64
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from .main import Knowledge, Settings, settings

logger = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    pass


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


class OpenAICompatibleProvider:
    """Provider-neutral interface over the OpenAI-compatible HTTP API shape."""

    def __init__(self, config: Settings = settings):
        self.config = config
        self.base_url = config.ai_api_base_url.rstrip("/")

    def _post(self, endpoint: str, payload: dict) -> dict:
        if not self.config.ai_api_key:
            raise AIProviderError("An AI API key is not configured")
        try:
            response = httpx.post(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                headers={"Authorization": f"Bearer {self.config.ai_api_key}"},
                json=payload,
                timeout=self.config.ai_timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise AIProviderError("The configured AI provider returned an invalid response")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Configured AI provider request failed: %s", type(exc).__name__)
            raise AIProviderError("The configured AI provider request failed") from exc

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not self.config.embedding_model:
            raise AIProviderError("EMBEDDING_MODEL is not configured")
        if not texts:
            return []
        result = self._post("embeddings", {"model": self.config.embedding_model, "input": [text[:8000] for text in texts]})
        try:
            rows = sorted(result["data"], key=lambda row: int(row["index"]))
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
            if len(vectors) != len(texts) or any(len(vector) != 1536 or not all(math.isfinite(value) for value in vector) for vector in vectors):
                raise ValueError("Embedding dimensions, count, or values are invalid")
            return vectors
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderError("The embedding provider returned an invalid vector") from exc

    def analyse(self, content: str, context: dict, sources: list[Knowledge]) -> AnalysisResponse:
        if not self.config.ai_model:
            raise AIProviderError("AI_MODEL is not configured")
        source_context = [
            {
                "id": source.id,
                "type": source.kind,
                "title": source.title,
                "rule_or_fact": source.body,
                "source_document": source.source,
                "approved_passage": source.passage,
                "version": source.version,
            }
            for source in sources
        ]
        schema = {
            "findings": [
                {
                    "kind": "claim|tone|terminology|legal|other",
                    "excerpt": "exact substring copied from the draft",
                    "explanation": "plain-language reason grounded in source IDs",
                    "severity": "low|medium|high|critical",
                    "confidence": 0.0,
                    "cited_source_ids": [1],
                    "cannot_verify": False,
                    "suggested_rewrite": "replacement for only the excerpt, or null",
                    "intent_preserved": False,
                }
            ]
        }
        system = (
            "You are a pre-publication brand compliance analyst. The only authoritative guidance is the supplied approved-source list. "
            "Never invent a rule, fact, source, quotation, or citation. Every finding must quote an exact substring from the draft. "
            "For claims, cite relevant approved fact IDs only; if evidence is absent, contradictory, or insufficient, set cannot_verify=true, "
            "leave cited_source_ids empty, and do not guess. For rule findings, cite relevant approved rule IDs. Do not flag style or dialect "
            "unless an approved rule supports it. Suggested rewrites must change only the excerpt and preserve its intended meaning; set "
            "intent_preserved=true only when that is genuinely satisfied. Return only JSON matching this shape: " + json.dumps(schema)
        )
        user_payload = {"draft": content[:50000], "intent": context, "approved_sources": source_context}
        result = self._post("chat/completions", {
            "model": self.config.ai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
        })
        try:
            raw = result["choices"][0]["message"]["content"]
            if isinstance(raw, list):
                raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
            return AnalysisResponse.model_validate_json(raw)
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise AIProviderError("The AI provider response did not match the required finding schema") from exc

    def analyse_image(self, image: bytes, mime_type: str, context: dict, sources: list[Knowledge], reference_logo: bytes | None = None, reference_mime_type: str = "image/png") -> AnalysisResponse:
        """Use a configured vision-capable model with approved rules and image context."""
        model = self.config.vision_model or self.config.ai_model
        if not model:
            raise AIProviderError("VISION_MODEL or AI_MODEL is not configured")
        if not sources:
            return AnalysisResponse()
        source_context = [{
            "id": source.id, "type": source.kind, "title": source.title,
            "rule_or_fact": source.body, "source_document": source.source,
            "approved_passage": source.passage, "version": source.version,
        } for source in sources]
        system = (
            "Review the supplied image only against the provided approved workspace sources and declared intent. "
            "Return only JSON using the required findings schema. Never invent brand rules, source IDs, contrast measurements, "
            "or logo requirements. Each finding must cite an approved source ID. Use an exact visible text excerpt, or a pixel "
            "region in the form 'Image region x=0 y=0 width=1 height=1' using coordinates within the supplied image. "
            "For claims, cite approved facts only and abstain when evidence is insufficient. Only suggest alt text or rewrites "
            "when the approved guidance supports the recommendation."
        )
        user_text = {"context": context, "approved_sources": source_context, "has_reference_logo": reference_logo is not None}
        content = [
            {"type": "text", "text": json.dumps(user_text, ensure_ascii=False)},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"}},
        ]
        if reference_logo:
            content.append({"type": "image_url", "image_url": {"url": f"data:{reference_mime_type};base64,{base64.b64encode(reference_logo).decode('ascii')}"}})
        result = self._post("chat/completions", {
            "model": model, "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        })
        try:
            raw = result["choices"][0]["message"]["content"]
            if isinstance(raw, list):
                raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
            return AnalysisResponse.model_validate_json(raw)
        except (KeyError, IndexError, TypeError, ValidationError) as exc:
            raise AIProviderError("The vision provider response did not match the required finding schema") from exc


def provider_from_config(config: Settings = settings) -> OpenAICompatibleProvider:
    if config.ai_provider.strip().lower() not in {"openai", "openai_compatible"} or not config.ai_api_key:
        raise AIProviderError("Configure AI_PROVIDER as openai_compatible and set AI_API_KEY to enable external analysis")
    return OpenAICompatibleProvider(config)

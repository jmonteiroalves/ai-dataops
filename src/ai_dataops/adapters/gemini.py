"""Vertex AI Gemini adapter.

Cloud SDK imports live only in this package (AGENTS.md). The class is instantiated by the
composition root in ``cli.py``.
"""

from __future__ import annotations

import time
from typing import Any

from ai_dataops.config import Settings
from ai_dataops.diagnosis.provider import DiagnosisRequest, DiagnosisResponse
from ai_dataops.diagnosis.schema import DiagnosisPayload
from ai_dataops.errors import ConfigurationError, ProviderError


class VertexGeminiProvider:
    """Gemini on Vertex AI using Application Default Credentials."""

    name = "vertex"

    def __init__(self, settings: Settings) -> None:
        if not settings.project_id:
            raise ConfigurationError("DATAOPS_PROJECT_ID is required for the Vertex provider")
        self.project_id = settings.project_id
        self.location = settings.gemini_location
        self.model_id = settings.gemini_model
        self.timeout_seconds = settings.diagnosis_timeout_seconds
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise ProviderError(
                    "google-genai is not installed; install the 'ai' extra"
                ) from exc
            self._client = genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.location,
                http_options=types.HttpOptions(timeout=self.timeout_seconds * 1000),
            )
        return self._client

    def generate(self, request: DiagnosisRequest) -> DiagnosisResponse:
        from google.genai import types

        client = self._get_client()
        started = time.perf_counter()
        response = None
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = client.models.generate_content(
                    model=self.model_id,
                    contents=request.user,
                    config=types.GenerateContentConfig(
                        system_instruction=request.system,
                        response_mime_type="application/json",
                        response_schema=DiagnosisPayload,
                        max_output_tokens=1500,
                    ),
                )
                break
            except Exception as exc:
                last_error = exc
        if response is None:
            raise ProviderError(
                f"Vertex AI call failed: {type(last_error).__name__}"
            ) from last_error

        latency_ms = int((time.perf_counter() - started) * 1000)
        text = response.text
        if not text:
            raise ProviderError("Vertex AI returned an empty response")
        usage = response.usage_metadata
        return DiagnosisResponse(
            text=text,
            model_id=self.model_id,
            model_version=str(getattr(response, "model_version", self.model_id)),
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            latency_ms=latency_ms,
        )


__all__ = ["VertexGeminiProvider"]

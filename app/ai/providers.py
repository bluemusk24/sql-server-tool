"""Provider-agnostic LLM surface (#18).

The correlation layer depends only on the small :class:`LLMProvider` protocol,
never on a vendor SDK. :class:`HttpLLMProvider` is the real implementation: an
OpenAI-compatible chat-completions call over ``httpx``, configured from the #2
``LLM_ENDPOINT``/``LLM_API_KEY`` settings.

Security: the API key travels only in the ``Authorization`` header and is never
logged, never placed in a URL, and never included in an exception message. All
failures are mapped onto the categorized :mod:`app.ai.errors` types.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from app.ai.errors import LLMError, LLMErrorCategory, LLMTimeoutError
from app.config import DEFAULT_LLM_TIMEOUT_SECONDS, Settings

#: Default model name sent to an OpenAI-compatible endpoint (deploy-time detail).
DEFAULT_LLM_MODEL = "gpt-3.5-turbo"

#: System instruction shared by every provider request.
SYSTEM_INSTRUCTION = (
    "You are a SQL Server diagnostics assistant. Correlate the supplied rule "
    "signals and evidence summaries into a probable cause. Reply with a single "
    "JSON object only. Never include credentials, passwords, or connection "
    "strings."
)


@runtime_checkable
class LLMProvider(Protocol):
    """The minimal provider surface the correlation layer calls."""

    def complete(self, prompt: str, *, timeout: float | None = None) -> str:
        """Return the provider's raw text response for ``prompt``.

        Raises :class:`~app.ai.errors.LLMTimeoutError` on timeout and
        :class:`~app.ai.errors.LLMError` on any other provider failure.
        """


class HttpLLMProvider:
    """OpenAI-compatible chat-completions provider over ``httpx``.

    ``transport`` is injectable so tests can drive the provider with an
    ``httpx.MockTransport`` and never touch the network.
    """

    name = "http"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str = DEFAULT_LLM_MODEL,
        timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, *, model: str = DEFAULT_LLM_MODEL
    ) -> HttpLLMProvider:
        """Build the provider from the #2 settings (endpoint, key, timeout)."""
        return cls(
            endpoint=str(settings.llm_endpoint),
            api_key=settings.llm_api_key,
            model=model,
            timeout=settings.llm_timeout_seconds,
        )

    def complete(self, prompt: str, *, timeout: float | None = None) -> str:
        """POST a chat-completions request and return the assistant's text."""
        effective_timeout = timeout if timeout is not None else self._timeout
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(
                timeout=effective_timeout, transport=self._transport
            ) as client:
                response = client.post(
                    f"{self._endpoint}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException:
            raise LLMTimeoutError("the LLM provider timed out") from None
        except httpx.HTTPError:
            raise LLMError(
                LLMErrorCategory.provider_error, "the LLM request could not be completed"
            ) from None

        if response.status_code >= 400:
            raise LLMError(
                LLMErrorCategory.provider_error,
                f"the LLM provider returned status {response.status_code}",
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError(
                LLMErrorCategory.malformed_json,
                "the LLM response envelope was not understood",
            ) from None
        if not isinstance(content, str):
            raise LLMError(
                LLMErrorCategory.malformed_json,
                "the LLM response had no text content",
            )
        return content

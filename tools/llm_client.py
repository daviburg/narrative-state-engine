#!/usr/bin/env python3
"""
llm_client.py — Thin wrapper around the OpenAI Chat Completions API.

Supports any OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Ollama, vLLM, etc.)
via configurable base_url in config/llm.json.
"""

import json
import os
import random
import re
import sys
import time


class LLMExtractionError(Exception):
    """Raised when the LLM response cannot be parsed or validated."""


class QuotaExhaustedError(LLMExtractionError):
    """Raised when consecutive rate-limit (429) errors suggest daily quota exhaustion."""


class RetryStats:
    """Track API call statistics across an LLMClient session."""

    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.retried_requests = 0
        self.errors_by_status: dict[int | str, int] = {}
        self.retry_after_seen = 0
        self.consecutive_rate_limits = 0
        self._max_consecutive_rate_limits = 0

    def record_success(self):
        self.total_requests += 1
        self.successful_requests += 1
        self.consecutive_rate_limits = 0

    def record_error(self, status_code: int | str | None, retry_after_present: bool = False):
        self.total_requests += 1
        key = status_code if status_code is not None else "unknown"
        self.errors_by_status[key] = self.errors_by_status.get(key, 0) + 1
        if retry_after_present:
            self.retry_after_seen += 1
        if key == 429:
            self.consecutive_rate_limits += 1
            self._max_consecutive_rate_limits = max(
                self._max_consecutive_rate_limits, self.consecutive_rate_limits)
        else:
            self.consecutive_rate_limits = 0

    def record_retry(self):
        self.retried_requests += 1

    def summary(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "retried_requests": self.retried_requests,
            "error_breakdown": dict(self.errors_by_status),
            "retry_after_headers_seen": self.retry_after_seen,
            "max_consecutive_rate_limits": self._max_consecutive_rate_limits,
        }

    def has_errors(self) -> bool:
        return bool(self.errors_by_status)


class LLMClient:
    """Thin wrapper around the OpenAI Chat Completions API."""

    def __init__(self, config_path: str = "config/llm.json", overrides: dict | None = None):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        if overrides:
            self.config.update(overrides)

        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "The 'openai' package is required for semantic extraction. "
                "Install it with: pip install -r requirements-llm.txt"
            )

        api_key_env = self.config.get("api_key_env")
        api_key = None
        if api_key_env:
            api_key = os.environ.get(api_key_env)
            if not api_key:
                raise LLMExtractionError(
                    f"Environment variable '{api_key_env}' is not set. "
                    f"Set it or configure a local provider in {config_path}."
                )

        # Disable SDK-level retries — we handle retries ourselves to avoid
        # the SDK's max_retries (default 2) multiplying with our retry_attempts,
        # which caused up to 9 attempts per call and wasted quota (#215).
        self.client = OpenAI(
            base_url=self.config.get("base_url", "https://api.openai.com/v1"),
            api_key=api_key or "not-needed",
            timeout=self.config.get("timeout_seconds", 60),
            max_retries=0,
        )
        self.model = self.config.get("model", "gpt-4o")
        self.temperature = self.config.get("temperature", 0.0)
        self.max_tokens = self.config.get("max_tokens", 4096)
        self.pc_max_tokens = self.config.get("pc_max_tokens", self.max_tokens)
        self.retry_attempts = self.config.get("retry_attempts", 3)
        self.batch_delay_ms = self.config.get("batch_delay_ms", 200)
        self.default_timeout = self.config.get("timeout_seconds", 60)
        self.context_length = self.config.get("context_length", None)
        self.ollama_options = self.config.get("ollama_options", None)
        self.ollama_format = self.config.get("ollama_format", None)
        self.consecutive_rate_limit_threshold = self.config.get(
            "consecutive_rate_limit_threshold", 10)
        self.stats = RetryStats()

    @property
    def _is_ollama(self) -> bool:
        """True when the configured provider is Ollama."""
        base_url = self.config.get("base_url", "")
        return (
            self.config.get("provider", "").lower() == "ollama"
            or ":11434" in base_url
        )

    @property
    def _skip_response_format(self) -> bool:
        """True when response_format should be omitted (e.g. qwen3.5 on Ollama)."""
        if self.config.get("skip_response_format"):
            return True
        # Ollama + qwen3.5 hangs with response_format=json_object
        if self._is_ollama and "qwen3.5" in self.model:
            return True
        return False

    @property
    def _is_cloud_provider(self) -> bool:
        """True when the configured provider appears to be a cloud API."""
        if self._is_ollama:
            return False
        base_url = self.config.get("base_url", "")
        return not any(local in base_url for local in [
            "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
        ])

    @staticmethod
    def _classify_error(e: Exception) -> tuple[int | None, float | None]:
        """Classify an API error and extract retry timing.

        Returns:
            (status_code, retry_after_seconds) where either can be None.
        """
        status_code = getattr(e, "status_code", None)
        retry_after = None

        response = getattr(e, "response", None)
        if response is not None:
            headers = getattr(response, "headers", {})
            # Check for Retry-After header (seconds)
            ra = headers.get("retry-after")
            if ra is not None:
                try:
                    retry_after = float(ra)
                except (ValueError, TypeError):
                    pass  # Non-numeric Retry-After (e.g. HTTP-date); ignore
            # Check for retry-after-ms header (milliseconds)
            ra_ms = headers.get("retry-after-ms")
            if ra_ms is not None and retry_after is None:
                try:
                    retry_after = float(ra_ms) / 1000.0
                except (ValueError, TypeError):
                    pass  # Non-numeric retry-after-ms; ignore

        # Detect RESOURCE_EXHAUSTED in message body as a 429 equivalent
        if status_code is None and "RESOURCE_EXHAUSTED" in str(e):
            status_code = 429

        return status_code, retry_after

    def _handle_retry(self, attempt: int, e: Exception, context: str = "") -> None:
        """Classify error, record stats, compute backoff, and sleep.

        Raises QuotaExhaustedError if consecutive rate limits exceed threshold.
        """
        status_code, retry_after = self._classify_error(e)
        has_retry_after = retry_after is not None
        self.stats.record_error(status_code, has_retry_after)

        # Log rate-limit errors with Retry-After visibility (#215)
        if status_code == 429:
            ra_msg = f"Retry-After: {retry_after:.1f}s" if has_retry_after else "no Retry-After header"
            print(f"  Rate limited (429), {ra_msg}{context}", file=sys.stderr)

            if self.stats.consecutive_rate_limits >= self.consecutive_rate_limit_threshold:
                raise QuotaExhaustedError(
                    f"Quota appears exhausted: {self.stats.consecutive_rate_limits} "
                    f"consecutive 429 errors. Stopping to avoid wasting requests."
                ) from e
        elif status_code in (503, 504):
            print(
                f"  Server error ({status_code}), retrying{context}",
                file=sys.stderr,
            )

        if attempt < self.retry_attempts - 1:
            self.stats.record_retry()
            if retry_after is not None and retry_after <= 60:
                backoff = retry_after
            else:
                backoff = min((2 ** attempt) * 1.0, 60.0)
            # Add jitter for cloud providers to avoid thundering herd
            if self._is_cloud_provider:
                backoff += random.uniform(0, 1)
            time.sleep(backoff)

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        timeout: int | None = None,
        max_tokens: int | None = None,
    ) -> dict | list:
        """Send a chat completion request and parse the JSON response.

        Args:
            system_prompt: Role instructions for the model.
            user_prompt: The turn text and context.
            schema: Optional JSON schema for response_format (structured outputs).
            timeout: Optional per-call timeout in seconds. Overrides the
                default timeout from config for this call only.
            max_tokens: Optional per-call max_tokens override. When provided,
                overrides ``self.max_tokens`` for this call only.

        Returns:
            Parsed JSON object/array.

        Raises:
            LLMExtractionError: If the response is not valid JSON or
                fails schema validation.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        last_error = None
        for attempt in range(self.retry_attempts):
            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
                }
                # Ollama hangs when response_format=json_object is used with
                # qwen3.5 models (thinking-mode conflict).  Skip it for those.
                if not self._skip_response_format:
                    kwargs["response_format"] = {"type": "json_object"}
                if timeout is not None:
                    kwargs["timeout"] = timeout
                if self._is_ollama:
                    options = dict(self.ollama_options) if self.ollama_options else {}
                    if self.context_length:
                        options["num_ctx"] = self.context_length
                    extra_body: dict = {}
                    if options:
                        extra_body["options"] = options
                    # Ollama native format parameter — distinct from OpenAI
                    # response_format.  Use "json" to constrain output to
                    # valid JSON without the thinking-mode hang seen with
                    # response_format on qwen3.5 models.
                    if self.ollama_format:
                        extra_body["format"] = self.ollama_format
                    if extra_body:
                        kwargs["extra_body"] = extra_body

                response = self.client.chat.completions.create(**kwargs)
                raw_text = response.choices[0].message.content

                if not raw_text:
                    raise LLMExtractionError("Empty response from LLM.")

                parsed = self._parse_json_response(raw_text)

                # Enforce dict when response_format is json_object
                if not isinstance(parsed, dict):
                    raise LLMExtractionError(
                        f"Expected JSON object but got {type(parsed).__name__}: "
                        f"{raw_text[:200]}"
                    )

                if schema:
                    import jsonschema
                    jsonschema.validate(parsed, schema)

                self.stats.record_success()
                return parsed

            except Exception as e:
                last_error = e
                self._handle_retry(attempt, e)

        raise LLMExtractionError(
            f"Failed after {self.retry_attempts} attempts. Last error: {last_error}"
        )

    def _parse_json_response(self, raw_text: str) -> dict | list:
        """Parse JSON from LLM response text, handling markdown fences."""
        text = raw_text.strip()

        # Strip <think>...</think> blocks (qwen3.5 thinking mode output)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Strip markdown code fences if present
        fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMExtractionError(
                f"Failed to parse JSON from LLM response: {e}\n"
                f"Raw response (first 500 chars): {raw_text[:500]}"
            )

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: int | None = None,
    ) -> str:
        """Send a chat completion request and return the raw text response.

        Unlike ``extract_json``, this does not enforce JSON formatting or
        parsing. Use this for free-form text generation (e.g., narrative
        biography synthesis).

        Args:
            system_prompt: Role instructions for the model.
            user_prompt: The user message / task description.
            timeout: Optional per-call timeout in seconds.

        Returns:
            The raw text content from the LLM response.

        Raises:
            LLMExtractionError: If the LLM returns an empty response or
                all retry attempts fail.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        last_error = None
        for attempt in range(self.retry_attempts):
            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
                if timeout is not None:
                    kwargs["timeout"] = timeout
                if self._is_ollama:
                    options = dict(self.ollama_options) if self.ollama_options else {}
                    if self.context_length:
                        options["num_ctx"] = self.context_length
                    extra_body_raw: dict = {}
                    if options:
                        extra_body_raw["options"] = options
                    if self.ollama_format:
                        extra_body_raw["format"] = self.ollama_format
                    if extra_body_raw:
                        kwargs["extra_body"] = extra_body_raw

                response = self.client.chat.completions.create(**kwargs)
                raw_text = response.choices[0].message.content

                if not raw_text:
                    raise LLMExtractionError("Empty response from LLM.")

                self.stats.record_success()
                return raw_text.strip()

            except Exception as e:
                last_error = e
                self._handle_retry(attempt, e)

        raise LLMExtractionError(
            f"Failed after {self.retry_attempts} attempts. Last error: {last_error}"
        )

    def delay(self) -> None:
        """Apply the configured batch delay between API calls.

        For cloud providers, enforces a minimum delay of 2000ms to avoid
        hitting per-minute rate limits (#215).
        """
        delay_ms = self.batch_delay_ms
        if self._is_cloud_provider:
            delay_ms = max(delay_ms, 2000)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

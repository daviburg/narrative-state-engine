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


class LLMTruncationError(LLMExtractionError):
    """Raised when the LLM response was truncated due to max_tokens limit.

    The partial response text is available via the `partial_text` attribute
    for callers that want to attempt JSON repair.
    """

    def __init__(self, message: str, partial_text: str = ""):
        super().__init__(message)
        self.partial_text = partial_text


class QuotaExhaustedError(LLMExtractionError):
    """Raised when consecutive rate-limit (429) errors suggest daily quota exhaustion."""


class RetryStats:
    """Track API call statistics across an LLMClient session."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self.total_requests = 0
        self.successful_requests = 0
        self.retried_requests = 0
        self.errors_by_status: dict[int | str, int] = {}
        self.retry_after_seen = 0
        self.consecutive_rate_limits = 0
        self._max_consecutive_rate_limits = 0

    def record_success(self):
        with self._lock:
            self.total_requests += 1
            self.successful_requests += 1
            self.consecutive_rate_limits = 0

    def record_error(self, status_code: int | str | None, retry_after_present: bool = False):
        with self._lock:
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
        with self._lock:
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
        try:
            self.parallel_workers = max(1, int(self.config.get("parallel_workers", 1)))
        except (ValueError, TypeError):
            self.parallel_workers = 1
        self.default_timeout = self.config.get("timeout_seconds", 60)
        self.context_length = self.config.get("context_length", None)
        self.ollama_options = self.config.get("ollama_options", None)
        self.ollama_format = self.config.get("ollama_format", None)
        self.consecutive_rate_limit_threshold = self.config.get(
            "consecutive_rate_limit_threshold", 10)
        self.stats = RetryStats()

        # Force sequential execution for cloud providers — parallel bursts
        # would trip rate limits / quota thresholds (#282 review).
        if self.parallel_workers > 1 and self._is_cloud_provider:
            self.parallel_workers = 1

    @property
    def _is_ollama(self) -> bool:
        """True when the configured provider is Ollama."""
        base_url = self.config.get("base_url", "")
        return (
            self.config.get("provider", "").lower() == "ollama"
            or ":11434" in base_url
        )

    @property
    def _use_ollama_streaming(self) -> bool:
        """True when we should use Ollama's native streaming API.

        Streaming via the native ``/api/chat`` endpoint avoids the
        non-streaming hang that occurs with qwen3.5 thinking-mode models
        when ``format=json`` or ``response_format`` is used through the
        OpenAI-compatible ``/v1`` endpoint.
        """
        return self._is_ollama and bool(self.ollama_format)

    @property
    def _ollama_native_url(self) -> str:
        """Derive the Ollama native chat endpoint from the configured base_url."""
        base = self.config.get("base_url", "http://localhost:11434/v1")
        # Strip /v1 suffix to get Ollama root, then append /api/chat
        if base.rstrip("/").endswith("/v1"):
            base = base.rstrip("/")[:-3]
        return base.rstrip("/") + "/api/chat"

    def _ollama_streaming_chat(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        timeout: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Call Ollama's native /api/chat with streaming.

        Returns the assembled visible content (with <think> blocks stripped).
        Raises LLMExtractionError on empty response or timeout.
        """
        import httpx

        body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature if temperature is not None else self.temperature,
                "num_predict": max_tokens or self.max_tokens,
            },
        }
        if self.context_length:
            body["options"]["num_ctx"] = self.context_length
        if self.ollama_options:
            body["options"].update(self.ollama_options)
        if self.ollama_format:
            body["format"] = self.ollama_format
        # Ollama top-level think parameter — False disables thinking so
        # all num_predict budget goes to visible output.
        ollama_think = self.config.get("ollama_think")
        if ollama_think is not None:
            body["think"] = ollama_think

        effective_timeout = timeout or self.default_timeout
        # Allow generous read timeout — streaming sends chunks continuously
        # so the read timeout is per-chunk, not total.
        httpx_timeout = httpx.Timeout(
            connect=10.0,
            read=float(effective_timeout),
            write=10.0,
            pool=10.0,
        )

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        eval_count = 0
        prompt_eval_count = 0
        done_reason = "?"
        start = time.time()
        hard_limit = effective_timeout * 3  # total wall-clock limit

        with httpx.stream(
            "POST", self._ollama_native_url, json=body, timeout=httpx_timeout,
        ) as resp:
            for line in resp.iter_lines():
                if time.time() - start > hard_limit:
                    break
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("done"):
                    eval_count = chunk.get("eval_count", 0)
                    prompt_eval_count = chunk.get("prompt_eval_count", 0)
                    done_reason = chunk.get("done_reason", "?")
                    break
                msg = chunk.get("message", {})
                # Ollama streams qwen3.5 thinking-mode output in a
                # separate "thinking" field while "content" stays empty.
                # Collect both so we can diagnose failures.
                part = msg.get("content", "")
                if part:
                    content_parts.append(part)
                thinking = msg.get("thinking", "")
                if thinking:
                    thinking_parts.append(thinking)

        elapsed = time.time() - start
        raw = "".join(content_parts)
        if not raw:
            thinking_text = "".join(thinking_parts)
            # Truncate thinking for log — can be very long
            thinking_preview = thinking_text[:500] if thinking_text else "(none)"
            print(
                f"  STREAM-DEBUG: empty response in {elapsed:.1f}s — "
                f"eval={eval_count} prompt_eval={prompt_eval_count} "
                f"done_reason={done_reason} "
                f"thinking_tokens={len(thinking_parts)} "
                f"content_tokens={len(content_parts)}\n"
                f"  THINKING: {thinking_preview}",
                file=sys.stderr,
            )
            raise LLMExtractionError("Empty response from LLM.")
        return raw

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
            "192.168.", "10.", "172.16.", "172.17.", "172.18.",
            "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
            "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
            "172.29.", "172.30.", "172.31.",
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
        temperature: float | None = None,
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
            temperature: Optional per-call temperature override. When provided,
                overrides ``self.temperature`` for this call only (#251).

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
                effective_max = max_tokens if max_tokens is not None else self.max_tokens
                effective_temp = temperature if temperature is not None else self.temperature

                # Ollama streaming path — uses native /api/chat with
                # format=json to avoid the non-streaming empty-response
                # problem on thinking-mode models (qwen3.5).
                if self._use_ollama_streaming:
                    raw_text = self._ollama_streaming_chat(
                        messages, max_tokens=effective_max, timeout=timeout,
                        temperature=effective_temp,
                    )
                else:
                    kwargs = {
                        "model": self.model,
                        "messages": messages,
                        "temperature": effective_temp,
                        "max_tokens": effective_max,
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
                        if self.ollama_format:
                            extra_body["format"] = self.ollama_format
                        if extra_body:
                            kwargs["extra_body"] = extra_body

                    response = self.client.chat.completions.create(**kwargs)
                    raw_text = response.choices[0].message.content
                    finish_reason = getattr(response.choices[0], "finish_reason", None)

                    if not raw_text:
                        raise LLMExtractionError("Empty response from LLM.")

                    # Detect token-limit truncation before attempting JSON parse
                    if finish_reason == "length":
                        raise LLMTruncationError(
                            f"Response truncated (finish_reason=length, "
                            f"max_tokens={effective_max})",
                            partial_text=raw_text,
                        )

                parsed = self._parse_json_response(raw_text)

                # Enforce top-level dict for all extraction calls — both
                # response_format=json_object and Ollama format=json
                # produce objects; arrays would break downstream code.
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

            except (LLMTruncationError, QuotaExhaustedError):
                raise
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
                if self._use_ollama_streaming:
                    raw_text = self._ollama_streaming_chat(
                        messages, timeout=timeout,
                    )
                else:
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

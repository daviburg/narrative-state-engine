#!/usr/bin/env python3
"""
llm_client.py — Thin wrapper around the OpenAI Chat Completions API.

Supports any OpenAI-compatible endpoint (OpenAI, Azure OpenAI, Ollama, vLLM, etc.)
via configurable base_url in config/llm.json.
"""

import json
import os
import re
import time


class LLMExtractionError(Exception):
    """Raised when the LLM response cannot be parsed or validated."""


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

        self.client = OpenAI(
            base_url=self.config.get("base_url", "https://api.openai.com/v1"),
            api_key=api_key or "not-needed",
            timeout=self.config.get("timeout_seconds", 60),
        )
        self.model = self.config.get("model", "gpt-4o")
        self.temperature = self.config.get("temperature", 0.0)
        self.max_tokens = self.config.get("max_tokens", 4096)
        self.retry_attempts = self.config.get("retry_attempts", 3)
        self.batch_delay_ms = self.config.get("batch_delay_ms", 200)
        self.default_timeout = self.config.get("timeout_seconds", 60)

    def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict | None = None,
        timeout: int | None = None,
    ) -> dict | list:
        """Send a chat completion request and parse the JSON response.

        Args:
            system_prompt: Role instructions for the model.
            user_prompt: The turn text and context.
            schema: Optional JSON schema for response_format (structured outputs).
            timeout: Optional per-call timeout in seconds. Overrides the
                default timeout from config for this call only.

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
                    "max_tokens": self.max_tokens,
                    "response_format": {"type": "json_object"},
                }
                if timeout is not None:
                    kwargs["timeout"] = timeout

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

                return parsed

            except Exception as e:
                last_error = e
                if attempt < self.retry_attempts - 1:
                    backoff = (2 ** attempt) * 1.0
                    time.sleep(backoff)

        raise LLMExtractionError(
            f"Failed after {self.retry_attempts} attempts. Last error: {last_error}"
        )

    def _parse_json_response(self, raw_text: str) -> dict | list:
        """Parse JSON from LLM response text, handling markdown fences."""
        text = raw_text.strip()

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

                response = self.client.chat.completions.create(**kwargs)
                raw_text = response.choices[0].message.content

                if not raw_text:
                    raise LLMExtractionError("Empty response from LLM.")

                return raw_text.strip()

            except Exception as e:
                last_error = e
                if attempt < self.retry_attempts - 1:
                    backoff = (2 ** attempt) * 1.0
                    time.sleep(backoff)

        raise LLMExtractionError(
            f"Failed after {self.retry_attempts} attempts. Last error: {last_error}"
        )

    def delay(self) -> None:
        """Apply the configured batch delay between API calls."""
        if self.batch_delay_ms > 0:
            time.sleep(self.batch_delay_ms / 1000.0)

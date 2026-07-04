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
import threading
import time
from collections import namedtuple
from datetime import datetime, timezone
from typing import Optional


# Result of an Ollama native streaming call (#501): the assembled visible
# completion plus the REAL backend token counts and stop reason from the final
# stream frame.  Surfaced to the caller (instead of discarded) so raw-IO
# capture records real decode/prompt tokens for streaming runs and can tee a
# record for truncated/empty completions before the caller raises.
_StreamResult = namedtuple(
    "_StreamResult", ["text", "eval_count", "prompt_eval_count", "done_reason"]
)


# Matches a <think>...</think> reasoning block: case-insensitive (some
# backends/models emit <Think> or <THINK>), DOTALL so the block's content
# can span multiple lines, and non-greedy so back-to-back blocks are each
# matched individually rather than one match spanning from the first
# opening tag to the last closing tag. Matches the empty-block case
# (`<think>\s*</think>`, e.g. qwen3.5 in "thinking" mode with the server's
# --reasoning flag NOT actually suppressing the tag) just as well as a
# block containing real reasoning text, since `.*?` matches zero or more
# characters.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def strip_thinking_blocks(text: Optional[str]) -> Optional[str]:
    """Strip all ``<think>...</think>`` reasoning blocks from LLM output.

    Some backends (observed: qwen3.5 in "thinking" mode via an
    OpenAI-compatible endpoint) prepend a literal ``<think>...</think>``
    block to every completion -- sometimes empty (``<think>\\n\\n</think>``)
    -- regardless of server-side ``--reasoning`` flags intended to suppress
    it. This must be treated as defense-in-depth on the CLIENT side: relying
    on upstream server configuration to disable the behavior is not
    sufficient, since that configuration can silently fail to take effect.

    Handles (see ``_THINK_BLOCK_RE``): case-insensitive tags, multi-line
    content (DOTALL), the empty-block case, multiple blocks in one
    response, and leading/trailing whitespace left behind after removal
    (stripped from the result).

    Returns ``text`` unchanged (still passed through ``str`` truthiness --
    e.g. ``None``/``""`` pass through as-is) if it is falsy, since callers
    such as ``generate_text``'s empty-response check expect to see the
    ORIGINAL falsy value, not a stripped one.
    """
    if not text:
        return text
    return _THINK_BLOCK_RE.sub("", text).strip()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~3 characters per token.

    BPE tokenizers (Qwen, Llama, GPT) average 2.5–3.5 characters per token.
    Used by the raw-IO capture (#477 step 1) ONLY as a fallback when the
    backend response does not expose a usage/token-count field; such counts
    are flagged ``*_tokens_estimated: true`` in the capture record.

    Empty text estimates to 0 tokens (an empty completion or prompt really did
    decode/consume nothing); this keeps the measurement honest for the exact
    empty/truncated failure modes the instrumentation exists to capture.  A
    non-empty string still floors at 1 so a short completion is never recorded
    as zero output tokens.
    """
    if not text:
        return 0
    return max(1, len(text) // 3)


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

    def record_terminal_error(self, key: str) -> None:
        """Record a terminal error (e.g. truncation, quota_exhausted) in errors_by_status.

        Unlike record_error(), this does not increment total_requests or
        reset consecutive_rate_limits — the request was already counted.
        """
        with self._lock:
            self.errors_by_status[key] = self.errors_by_status.get(key, 0) + 1

    def has_errors(self) -> bool:
        return bool(self.errors_by_status)


class LLMClient:
    """Thin wrapper around the OpenAI Chat Completions API."""

    def __init__(self, config_path: str = "config/llm.json", overrides: dict | None = None):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = json.load(f)

        if overrides:
            self.config.update(overrides)
            # When base_url is explicitly overridden WITHOUT base_urls, drop
            # base_urls so the caller's single-endpoint intent is honoured
            # instead of the multi-endpoint round-robin list from the config
            # file.  When overrides also contain base_urls (e.g. internal
            # fallback init passing a full merged config), preserve them.
            if "base_url" in overrides and "base_urls" not in overrides:
                self.config.pop("base_urls", None)

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
        #
        # Multi-endpoint support: when "base_urls" (list) is configured,
        # create one OpenAI client per endpoint and round-robin across them.
        # Falls back to single "base_url" when the list is absent.
        base_urls = self.config.get("base_urls")
        if base_urls:
            if not isinstance(base_urls, list) or not all(
                isinstance(u, str) for u in base_urls
            ):
                raise LLMExtractionError(
                    "'base_urls' in llm.json must be a list of URL strings."
                )
            if not base_urls:
                raise LLMExtractionError(
                    "'base_urls' in llm.json must not be empty."
                )
        else:
            base_urls = [self.config.get("base_url", "https://api.openai.com/v1")]
        self._base_urls = base_urls
        self._clients = [
            OpenAI(
                base_url=url,
                api_key=api_key or "not-needed",
                timeout=self.config.get("timeout_seconds", 60),
                max_retries=0,
            )
            for url in base_urls
        ]
        self._client_index = 0
        self._client_lock = threading.Lock()
        # Keep self.client as primary for backward compatibility (Ollama paths, etc.)
        self.client = self._clients[0]
        self.model = self.config.get("model", "gpt-4o")
        self.temperature = self.config.get("temperature", 0.0)
        # Optional explicit sampler params.  Default None when absent so the
        # request body is byte-identical to the historical behaviour (no new
        # fields).  When set, they pin greedy/deterministic decoding — this
        # matters because some backends bake a stochastic default (e.g.
        # llama-server defaults to temperature 1.0), so omitting samplers
        # silently leaks server-side randomness into "temp 0" runs.
        self.top_k = self.config.get("top_k", None)
        self.top_p = self.config.get("top_p", None)
        self.min_p = self.config.get("min_p", None)
        self.seed = self.config.get("seed", None)
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

        # Raw-IO capture (#477 step 1) — measurement-only, default-OFF.
        # Remains None unless an extraction entry point explicitly calls
        # enable_raw_io_capture(path) (which it does ONLY when the default-OFF
        # context_optimizations.raw_io_capture flag is on).  While None,
        # extract_json/generate_text do ZERO capture work — no record is built,
        # serialised, or written — so a flag-OFF run is byte-identical to the
        # baseline with no added overhead.
        self._raw_io_capture_path: str | None = None
        self._raw_io_capture_lock = threading.Lock()

        # Fallback LLM provider — used when primary exhausts retries.
        # Configured via a "fallback" block in llm.json.
        self._fallback_client = None
        fallback_cfg = self.config.get("fallback")
        if fallback_cfg and not overrides:
            # Build a merged config for the fallback client
            fb_config = dict(self.config)
            fb_config.pop("fallback", None)  # prevent recursion
            # Clear primary base_urls so fallback uses its own base_url
            # unless the fallback block explicitly provides base_urls.
            if "base_urls" not in fallback_cfg:
                fb_config.pop("base_urls", None)
            fb_config.update(fallback_cfg)
            try:
                self._fallback_client = LLMClient.__new__(LLMClient)
                self._fallback_client.__init__(
                    config_path=config_path,
                    overrides=fb_config,
                )
            except Exception:
                self._fallback_client = None

        # Force sequential execution for cloud providers — parallel bursts
        # would trip rate limits / quota thresholds (#282 review).
        if self.parallel_workers > 1 and self._is_cloud_provider:
            self.parallel_workers = 1

        if len(self._clients) > 1:
            print(
                f"  Multi-endpoint: {len(self._clients)} endpoints configured, "
                f"round-robin dispatch enabled",
                file=sys.stderr,
            )

        # Sampler observability (#471): self-document the effective sampling
        # this run will use, so a future "temp 0 that wasn't really 0" is
        # immediately visible in the logs.  Best-effort; never fatal.
        self._log_sampler_config()

    def _log_sampler_config(self, probe_backend: bool | None = None) -> None:
        """Log the effective sampler configuration (#471) — best-effort.

        Emits two records to stderr:
          1. The sampler params THIS client threads into its requests. Both
             call paths (``extract_json`` and ``generate_text``) thread the
             same configured samplers, so this record applies to either
             method. Note the *send* path still drops backend-incompatible
             keys (e.g. the Ollama native streaming path forwards only
             ``temperature``); such values are annotated ``(not sent)`` below
             so the record reflects what actually reaches the backend.
          2. The backend's effective defaults from llama-server's ``/props``,
             when reachable (self-hosted backends only).

        Together they make every run's log self-document its sampling, closing
        the gap where a stochastic run masqueraded as deterministic. Never
        raises — any failure is swallowed so it cannot break extraction.

        Args:
            probe_backend: Whether to make the ``/props`` HTTP probe.  When
                None (the default), it is auto-enabled for self-hosted
                backends but suppressed under pytest so the unit suite stays
                offline.  Tests pass an explicit bool to exercise the probe.
        """
        try:
            base_url = self._base_urls[0] if self._base_urls else self.config.get(
                "base_url", "")
        except Exception:
            base_url = self.config.get("base_url", "")
        try:
            rr = (f" (round-robin x{len(self._base_urls)})"
                  if len(self._base_urls) > 1 else "")
            # Report only what THIS client actually transmits.  The send path
            # drops some samplers depending on the backend, so echoing raw
            # config would misrepresent the request:
            #   * Ollama streaming (/api/chat): only temperature is forwarded;
            #     top_k/top_p/min_p/seed are not sent.
            #   * Ollama OpenAI-compat (/v1): top_p and seed are native, but
            #     top_k/min_p are NOT forwarded (no extra_body sampler path).
            #   * Self-hosted OpenAI-compat (llama-server/vLLM): top_p and seed
            #     are native; top_k/min_p ride in extra_body, which these
            #     backends read, so all configured samplers are sent.
            #   * Cloud OpenAI-compatible APIs (e.g. api.openai.com): top_p and
            #     seed are native, but top_k/min_p are NOT sent — they are not
            #     part of the OpenAI schema and cloud providers reject unknown
            #     extra_body params (HTTP 400), so the send path drops them.
            # Configured-but-dropped values are annotated "(not sent)" so the
            # log never claims a sampler that did not reach the backend.
            if self._use_ollama_streaming:
                sent = {"top_k": False, "top_p": False,
                        "min_p": False, "seed": False}
            elif self._is_ollama:
                sent = {"top_k": False, "top_p": True,
                        "min_p": False, "seed": True}
            else:
                # top_k/min_p ride in extra_body, which only self-hosted
                # OpenAI-compatible backends accept; cloud APIs reject them.
                forward_extra = not self._is_cloud_provider
                sent = {"top_k": forward_extra, "top_p": True,
                        "min_p": forward_extra, "seed": True}

            def _fmt(name: str, value) -> str:
                if value is not None and not sent[name]:
                    return f"{name}={value}(not sent)"
                return f"{name}={value}"

            print(
                "  [sampler] INFO client effective sampling: "
                f"model={self.model} temperature={self.temperature} "
                f"{_fmt('top_k', self.top_k)} {_fmt('top_p', self.top_p)} "
                f"{_fmt('min_p', self.min_p)} {_fmt('seed', self.seed)} "
                f"max_tokens={self.max_tokens} "
                f"base_url={base_url}{rr}",
                file=sys.stderr,
            )
        except Exception:
            # Best-effort observability only: logging the sampler config must
            # never break extraction, so any formatting/IO error is swallowed.
            pass

        # Probe llama-server /props for the SERVER-side effective defaults.
        # Only for self-hosted backends — cloud APIs have no such endpoint and
        # we must not make spurious network calls to them.  Suppressed under
        # pytest to keep the unit suite offline.  llama-server serves /props at
        # the ROOT, not under /v1, so strip a trailing /v1.
        #
        # Cloud providers are NEVER probed, even when a caller forces
        # ``probe_backend=True``: cloud APIs have no /props endpoint and we
        # must not make spurious network calls (e.g. to api.openai.com/props).
        if self._is_cloud_provider:
            return
        if probe_backend is None:
            probe_backend = "PYTEST_CURRENT_TEST" not in os.environ
        if not probe_backend:
            return
        try:
            import httpx

            server_root = base_url.rstrip("/")
            if server_root.endswith("/v1"):
                server_root = server_root[:-3]
            resp = httpx.get(server_root.rstrip("/") + "/props", timeout=5.0)
            if resp.status_code == 200:
                params = (
                    resp.json()
                    .get("default_generation_settings", {})
                    .get("params", {})
                )
                keys = ("temperature", "top_k", "top_p", "min_p", "seed", "samplers")
                summary = {k: params.get(k) for k in keys if k in params}
                print(
                    f"  [sampler] INFO backend /props effective defaults: {summary}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  [sampler] DEBUG /props probe returned HTTP "
                    f"{resp.status_code}; backend sampler defaults unknown.",
                    file=sys.stderr,
                )
        except Exception as e:
            print(
                f"  [sampler] DEBUG /props probe unavailable "
                f"({type(e).__name__}); backend sampler defaults unknown.",
                file=sys.stderr,
            )

    def _next_client(self):
        """Return the next OpenAI client in round-robin order (thread-safe)."""
        if len(self._clients) == 1:
            return self._clients[0]
        with self._client_lock:
            client = self._clients[self._client_index]
            self._client_index = (self._client_index + 1) % len(self._clients)
            return client

    def enable_raw_io_capture(self, path: str) -> None:
        """Enable raw-IO capture (#477 step 1), teeing one JSONL record per
        LLM call to *path*.

        MEASUREMENT-ONLY: this never changes the prompts, the calls, the
        parsing, or any extracted output.  It only WRITES an extra artifact
        containing the verbatim prompt + completion (plus per-call input/output
        token counts) the client already holds, so a downstream analysis can
        verify what the entity_detail phase actually emits — closing the
        parsed-catalog-proxy gap.  Called by the extraction entry points only
        when the default-OFF ``raw_io_capture`` flag is on; while it is never
        called, the client does no capture work at all.
        """
        self._raw_io_capture_path = path
        try:
            d = os.path.dirname(os.path.abspath(path))
            if d:
                os.makedirs(d, exist_ok=True)
        except OSError:
            # Capture is best-effort: a dir-creation failure must never break
            # extraction.  The per-record writer also guards its own IO.
            pass
        # Propagate to the fallback provider so completions served by fallback
        # (after the primary exhausts its retries) are ALSO captured (#501
        # finding 5 — fallback-served catalogs must not be a blind spot).  The
        # fallback shares the primary's lock so concurrent appends to the one
        # shared artifact stay mutually exclusive (no interleaved JSONL lines).
        if getattr(self, "_fallback_client", None) is not None:
            self._fallback_client.enable_raw_io_capture(path)
            self._fallback_client._raw_io_capture_lock = self._raw_io_capture_lock

    def _write_raw_io_record(
        self, capture: dict, messages: list, raw_text: str, response,
        output_tokens: int | None = None, input_tokens: int | None = None,
    ) -> None:
        """Append one raw-IO capture record (#477 step 1) — best-effort.

        Records the verbatim system+user prompt, the verbatim un-parsed
        completion, and the per-call input/output token counts so a downstream
        analysis can verify (on real data, not a parsed-catalog proxy) what the
        entity_detail phase truly emits.  Output (decode) tokens come from the
        backend's REAL count in priority order: an explicit ``output_tokens``
        override (the Ollama streaming ``eval_count`` from the final frame),
        then ``usage.completion_tokens`` (non-streaming OpenAI-compatible);
        only when neither is present is a char-heuristic estimate used and the
        record flagged ``output_tokens_estimated: true``.  Input tokens follow
        the same rule via ``input_tokens`` / ``usage.prompt_tokens`` (#501 F3).

        This is called once per LLM call BEFORE JSON parse / empty / truncation
        handling, so failed, truncated, parse-rejected, and retried completions
        are all captured (#501 finding 1 — one JSONL record per LLM call).

        Never raises: any serialisation or IO error is swallowed so capture can
        never interrupt extraction.
        """
        path = self._raw_io_capture_path
        if not path:
            return
        try:
            system_prompt = ""
            user_prompt = ""
            for m in messages:
                role = m.get("role")
                if role == "system":
                    system_prompt = m.get("content", "") or ""
                elif role == "user":
                    user_prompt = m.get("content", "") or ""

            output_tokens_val = None
            input_tokens_val = None
            output_tokens_estimated = True
            input_tokens_estimated = True
            # 1) Explicit real backend counts (Ollama streaming final frame).
            if isinstance(output_tokens, int):
                output_tokens_val = output_tokens
                output_tokens_estimated = False
            if isinstance(input_tokens, int):
                input_tokens_val = input_tokens
                input_tokens_estimated = False
            # 2) OpenAI-compatible usage object (non-streaming path).
            usage = getattr(response, "usage", None) if response is not None else None
            if usage is not None:
                if output_tokens_val is None:
                    ct = getattr(usage, "completion_tokens", None)
                    if isinstance(ct, int):
                        output_tokens_val = ct
                        output_tokens_estimated = False
                if input_tokens_val is None:
                    pt = getattr(usage, "prompt_tokens", None)
                    if isinstance(pt, int):
                        input_tokens_val = pt
                        input_tokens_estimated = False
            # 3) Char-heuristic estimate (flagged) when no real count exists.
            if output_tokens_val is None:
                output_tokens_val = _estimate_tokens(raw_text or "")
            if input_tokens_val is None:
                input_tokens_val = _estimate_tokens(
                    (system_prompt + "\n" + user_prompt) if (system_prompt or user_prompt) else ""
                )

            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "turn": capture.get("turn"),
                "phase": capture.get("phase"),
                "entity_id": capture.get("entity_id"),
                # All entity ids a single batched call covers (#501 finding 3);
                # None for non-batched calls so each batched record stays
                # attributable to the entities its prompt actually presented.
                "entity_ids": capture.get("entity_ids"),
                "raw_prompt": {"system": system_prompt, "user": user_prompt},
                "raw_completion": raw_text,
                "input_tokens": input_tokens_val,
                "output_tokens": output_tokens_val,
                "input_tokens_estimated": input_tokens_estimated,
                "output_tokens_estimated": output_tokens_estimated,
            }
            # ensure_ascii=False keeps non-ASCII prompt/completion text verbatim
            # and human-readable in the JSONL artifact (still valid UTF-8 JSON).
            line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
            with self._raw_io_capture_lock:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except Exception:
            # Best-effort measurement: never let capture break extraction.
            return


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
    ) -> "_StreamResult":
        """Call Ollama's native /api/chat with streaming.

        Returns a ``_StreamResult`` carrying the assembled visible content
        (with <think> blocks stripped) plus the REAL ``eval_count`` /
        ``prompt_eval_count`` and ``done_reason`` from the final stream frame.
        Empty-response and ``done_reason == "length"`` truncation are NOT raised
        here (the caller handles them after teeing a capture record, #501);
        only a watchdog abort (no completion at all) raises.
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
        done_received = False
        start = time.time()
        hard_limit = effective_timeout * 3  # total wall-clock limit

        # Mutable state shared with watchdog timer closure
        _wd_state = {"watchdog_fired": False}

        def _abort_stream(response, state):
            """Force-close connection to unblock iter_lines()."""
            state["watchdog_fired"] = True
            print(
                f"  WATCHDOG: aborting stalled Ollama stream after "
                f"{hard_limit:.0f}s",
                file=sys.stderr,
            )
            try:
                response.close()
            except Exception:
                pass  # Best-effort close — connection may already be dead

        with httpx.stream(
            "POST", self._ollama_native_url, json=body, timeout=httpx_timeout,
        ) as resp:
            watchdog = threading.Timer(
                hard_limit, _abort_stream, args=[resp, _wd_state]
            )
            watchdog.daemon = True
            watchdog.start()
            try:
                for line in resp.iter_lines():
                    if time.time() - start > hard_limit:
                        print(
                            f"  WATCHDOG: aborting stalled Ollama stream after "
                            f"{hard_limit:.0f}s",
                            file=sys.stderr,
                        )
                        break
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("done"):
                        done_received = True
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
            finally:
                watchdog.cancel()

        elapsed = time.time() - start
        raw = "".join(content_parts)

        # Guard: if watchdog fired before a done frame, content is truncated
        if _wd_state["watchdog_fired"] and not done_received:
            raise LLMExtractionError(
                "WATCHDOG: Stream aborted before completion"
            )

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

        # Empty-response and token-limit truncation (done_reason == "length")
        # are surfaced to the caller via the returned text / done_reason rather
        # than raised here (#501): the caller tees ONE raw-IO capture record
        # carrying the real eval/prompt token counts BEFORE raising the
        # identical LLMExtractionError / LLMTruncationError, so empty and
        # truncated streaming completions are not dropped from the measurement
        # set.  Behaviour is otherwise unchanged.
        return _StreamResult(raw, eval_count, prompt_eval_count, done_reason)

    def _call_with_deadline(self, fn, deadline_seconds: float):
        """Call fn() with a hard wall-clock deadline.

        Uses a daemon thread to enforce a maximum wall-clock time. If the
        deadline expires, raises LLMExtractionError so retry logic engages.
        The stalled thread is abandoned (daemon=True prevents blocking exit).
        """
        result_holder: dict = {}

        def _run():
            try:
                result_holder["result"] = fn()
            except Exception as e:
                result_holder["error"] = e

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout=deadline_seconds)
        if worker.is_alive():
            print(
                f"  WATCHDOG: LLM call exceeded {deadline_seconds:.0f}s "
                f"wall-clock deadline",
                file=sys.stderr,
            )
            raise LLMExtractionError(
                f"WATCHDOG: LLM call exceeded {deadline_seconds:.0f}s "
                f"wall-clock deadline"
            )
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder["result"]

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
        """True when the configured provider appears to be a cloud API.

        Resolution order (most explicit wins):
          1. An explicit ``self_hosted`` config flag overrides everything.
             Set ``"self_hosted": true`` for a self-hosted backend reachable
             via a public hostname or public IP (so top_k/min_p are still
             forwarded), or ``false`` to force cloud handling. This closes the
             gap where the URL heuristic below would misclassify a self-hosted
             llama-server/vLLM reached by DNS name or public address as cloud
             and silently drop its top_k/min_p samplers (#472 review).
          2. Ollama is always self-hosted.
          3. A known self-hosted ``provider`` name (llama-server, vllm, ...)
             is treated as non-cloud regardless of URL.
          4. Otherwise fall back to a URL heuristic: checks all endpoints in
             the client pool (base_urls) when configured, falling back to
             base_url, and returns True only if ALL endpoints appear to be
             cloud (non-local) URLs.
        """
        explicit = self.config.get("self_hosted")
        if isinstance(explicit, bool):
            return not explicit
        if self._is_ollama:
            return False
        provider = self.config.get("provider", "").lower()
        self_hosted_providers = {
            "llama", "llama-server", "llama.cpp", "llamacpp", "llama_cpp",
            "vllm", "tgi", "local", "self_hosted", "self-hosted",
        }
        if provider in self_hosted_providers:
            return False
        urls = getattr(self, '_base_urls', None) or [self.config.get("base_url", "")]
        local_patterns = [
            "localhost", "127.0.0.1", "0.0.0.0", "[::1]",
            "192.168.", "10.", "172.16.", "172.17.", "172.18.",
            "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
            "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
            "172.29.", "172.30.", "172.31.",
        ]
        return not any(
            any(local in url for local in local_patterns)
            for url in urls
        )

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
                self.stats.record_terminal_error("quota_exhausted")
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
        capture: dict | None = None,
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
            capture: Optional raw-IO capture tag dict (#477 step 1) with keys
                ``turn``, ``phase``, and ``entity_id``.  When raw-IO capture is
                enabled (see ``enable_raw_io_capture``) AND this is provided, the
                verbatim prompt + completion and per-call token counts are teed
                to the capture artifact.  Measurement-only: it never affects the
                request, parsing, or return value; when capture is disabled this
                argument is ignored at zero cost.

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

        # --- Input budget pre-flight check ---
        # Estimate whether input + requested output can fit in the context
        # window.  Uses a conservative 3 chars/token heuristic.  Emits
        # WARNING on overflow and NOTICE on tight fits (< 5% headroom).
        effective_max = max_tokens if max_tokens is not None else self.max_tokens
        if self.context_length:
            input_chars = len(system_prompt) + len(user_prompt)
            estimated_input = max(1, input_chars // 3)
            # ~50 tokens for chat-template framing (role markers, <think> block)
            estimated_total = estimated_input + effective_max + 50
            headroom = self.context_length - estimated_total
            if headroom < 0:
                print(
                    f"  WARNING: Input ({estimated_input} tok) + output "
                    f"({effective_max} tok) exceeds context window "
                    f"({self.context_length} tok) by ~{-headroom} tokens. "
                    f"Output will likely be truncated.",
                    file=sys.stderr,
                )
            elif headroom < self.context_length * 0.05:
                print(
                    f"  NOTICE: Tight context budget — input ~{estimated_input} "
                    f"tok + output {effective_max} tok, only ~{headroom} tok "
                    f"headroom in {self.context_length} tok window.",
                    file=sys.stderr,
                )

        last_error = None
        for attempt in range(self.retry_attempts):
            try:
                effective_temp = temperature if temperature is not None else self.temperature

                # response stays None on the Ollama streaming path (no usage
                # object); real token counts ride in stream_out/in_tokens
                # instead so capture records them (not estimates, #501 F3).
                response = None
                stream_out_tokens = None
                stream_in_tokens = None
                used_streaming = self._use_ollama_streaming
                # Ollama streaming path — uses native /api/chat with
                # format=json to avoid the non-streaming empty-response
                # problem on thinking-mode models (qwen3.5).
                if used_streaming:
                    stream_result = self._ollama_streaming_chat(
                        messages, max_tokens=effective_max, timeout=timeout,
                        temperature=effective_temp,
                    )
                    raw_text = stream_result.text
                    # 0 means "not reported by backend" → fall back to estimate.
                    stream_out_tokens = stream_result.eval_count or None
                    stream_in_tokens = stream_result.prompt_eval_count or None
                    finish_reason = stream_result.done_reason
                else:
                    kwargs = {
                        "model": self.model,
                        "messages": messages,
                        "temperature": effective_temp,
                        "max_tokens": effective_max,
                    }
                    # Explicit sampler params (#471) — pin deterministic
                    # decoding and prevent server-default sampler leakage.
                    # Some backends bake a stochastic default (llama-server
                    # defaults to temperature 1.0), so any omitted sampler
                    # silently inherits server randomness.  top_p and seed are
                    # native OpenAI params; top_k and min_p are not, so they
                    # ride in extra_body, which self-hosted OpenAI-compatible
                    # backends (llama-server, vLLM) read.  Cloud providers
                    # reject unknown extra_body params (HTTP 400), so top_k/
                    # min_p are NOT sent there.  All are sent only when
                    # configured, keeping the body byte-identical otherwise.
                    if self.top_p is not None:
                        kwargs["top_p"] = self.top_p
                    if self.seed is not None:
                        kwargs["seed"] = self.seed
                    sampler_extra: dict = {}
                    # top_k/min_p are non-standard params carried in extra_body,
                    # which only self-hosted OpenAI-compatible backends accept.
                    # Cloud providers reject unknown params (HTTP 400), so omit
                    # them there and keep the request schema-valid.
                    if not self._is_cloud_provider:
                        if self.top_k is not None:
                            sampler_extra["top_k"] = self.top_k
                        if self.min_p is not None:
                            sampler_extra["min_p"] = self.min_p
                    # Ollama hangs when response_format=json_object is used with
                    # qwen3.5 models (thinking-mode conflict).  Skip it for those.
                    if not self._skip_response_format:
                        kwargs["response_format"] = {"type": "json_object"}
                    if timeout is not None:
                        kwargs["timeout"] = timeout
                    if self._is_ollama:
                        # Note: top_k/min_p are not forwarded on the Ollama path
                        # (Ollama uses its own options mapping); only the
                        # llama-server/OpenAI path threads them via extra_body.
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
                    elif sampler_extra:
                        kwargs["extra_body"] = sampler_extra

                    hard_deadline = (timeout or self.default_timeout) * 3
                    response = self._call_with_deadline(
                        lambda: self._next_client().chat.completions.create(**kwargs),
                        hard_deadline,
                    )
                    raw_text = response.choices[0].message.content
                    finish_reason = getattr(response.choices[0], "finish_reason", None)

                # One raw-IO capture record per LLM call (#501 finding 1): tee
                # the verbatim completion BEFORE parse / empty / truncation
                # handling so failed, truncated, parse-rejected, and retried
                # completions are not dropped from the measurement set.  Real
                # backend token counts are used (streaming eval/prompt counts or
                # the usage object); measurement-only, skipped at zero cost when
                # capture is disabled.
                if capture is not None and self._raw_io_capture_path is not None:
                    self._write_raw_io_record(
                        capture, messages, raw_text or "", response,
                        output_tokens=stream_out_tokens,
                        input_tokens=stream_in_tokens,
                    )

                if not raw_text:
                    raise LLMExtractionError("Empty response from LLM.")

                # Detect token-limit truncation before attempting JSON parse.
                if finish_reason == "length":
                    if used_streaming:
                        raise LLMTruncationError(
                            f"Response truncated (done_reason=length, "
                            f"num_predict={effective_max or self.max_tokens})",
                            partial_text=raw_text,
                        )
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

            except LLMTruncationError:
                self.stats.record_terminal_error("truncation")
                raise
            except QuotaExhaustedError:
                raise
            except Exception as e:
                last_error = e
                self._handle_retry(attempt, e)

        # Primary provider exhausted — try fallback if configured.
        if self._fallback_client is not None:
            try:
                print(
                    f"  PRIMARY FAILED ({self.retry_attempts} attempts), "
                    f"trying fallback provider ({self._fallback_client.model})...",
                    file=sys.stderr,
                )
                result = self._fallback_client.extract_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    timeout=timeout,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    capture=capture,
                )
                self.stats.record_success()
                return result
            except LLMTruncationError:
                self.stats.record_terminal_error("truncation")
                raise
            except QuotaExhaustedError:
                raise
            except Exception as fb_err:
                raise LLMExtractionError(
                    f"Failed after {self.retry_attempts} primary attempts "
                    f"and fallback attempt. Primary: {last_error} | "
                    f"Fallback: {fb_err}"
                ) from fb_err

        raise LLMExtractionError(
            f"Failed after {self.retry_attempts} attempts. Last error: {last_error}"
        )

    def _parse_json_response(self, raw_text: str) -> dict | list:
        """Parse JSON from LLM response text, handling markdown fences."""
        text = raw_text.strip()

        # Strip <think>...</think> blocks (qwen3.5 thinking mode output)
        text = strip_thinking_blocks(text)

        # Strip markdown code fences if present
        fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        # Fix malformed confidence values like "confidence": 0-1.0 or 0-9 (#290)
        text = re.sub(
            r'"confidence":\s*(\d+)-(\d+(?:\.\d+)?)',
            self._fix_malformed_confidence,
            text,
        )

        try:
            return json.loads(text)
        except json.JSONDecodeError as initial_err:
            initial_error = initial_err

        # Fallback: model may have emitted reasoning text before/after JSON.
        # Use json.JSONDecoder().raw_decode which correctly handles braces
        # inside string literals and continues scanning on failure.
        decoder = json.JSONDecoder()
        search_start = 0
        last_candidate_err = None
        while search_start < len(text):
            brace_pos = text.find("{", search_start)
            if brace_pos == -1:
                break
            try:
                obj, end_idx = decoder.raw_decode(text, brace_pos)
                return obj
            except json.JSONDecodeError as e:
                last_candidate_err = e
                # Skip past this position and try the next '{'
                search_start = brace_pos + 1

        detail = f"Initial parse error: {initial_error}"
        if last_candidate_err and last_candidate_err is not initial_error:
            detail += f"\nFallback candidate error: {last_candidate_err}"
        raise LLMExtractionError(
            f"Failed to parse JSON from LLM response: no valid JSON object found.\n"
            f"{detail}\n"
            f"Raw response (first 500 chars): {raw_text[:500]}"
        )

    @staticmethod
    def _fix_malformed_confidence(match: re.Match) -> str:
        """Convert malformed confidence like 0-1.0 or 0-9 to a valid float.

        Only repairs patterns where the left side is "0", which covers the
        observed model output defects. Other patterns (e.g. "1-2") are left
        unchanged to avoid manufacturing incorrect confidence scores.
        """
        left = match.group(1)   # e.g. "0"
        right = match.group(2)  # e.g. "1.0" or "9"
        if left != "0":
            # Not a known malformation pattern — return original text unchanged
            return match.group(0)
        try:
            r = float(right)
            if r <= 1.0:
                # "0-0.95" → use right value as the confidence
                return f'"confidence": {r}'
            else:
                # "0-9" → model meant 0.9 (mistyped decimal)
                val = float(f"0.{right.replace('.', '')}")
                return f'"confidence": {min(val, 1.0)}'
        except (ValueError, TypeError):
            return '"confidence": 0.5'

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout: int | None = None,
        capture: dict | None = None,
    ) -> str:
        """Send a chat completion request and return the raw text response.

        Unlike ``extract_json``, this does not enforce JSON formatting or
        parsing. Use this for free-form text generation (e.g., narrative
        biography synthesis).

        Args:
            system_prompt: Role instructions for the model.
            user_prompt: The user message / task description.
            timeout: Optional per-call timeout in seconds.
            capture: Optional raw-IO capture tag dict (#477 step 1); see
                ``extract_json``.  Measurement-only and ignored at zero cost
                when capture is disabled.

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
                # response stays None on the Ollama streaming path (no usage
                # object); real token counts ride in stream_out/in_tokens so
                # capture records them (not estimates, #501 F3).
                response = None
                stream_out_tokens = None
                stream_in_tokens = None
                if self._use_ollama_streaming:
                    stream_result = self._ollama_streaming_chat(
                        messages, timeout=timeout,
                    )
                    raw_text = stream_result.text
                    stream_out_tokens = stream_result.eval_count or None
                    stream_in_tokens = stream_result.prompt_eval_count or None
                else:
                    kwargs = {
                        "model": self.model,
                        "messages": messages,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                    }
                    # Explicit sampler params (#471/#472) — thread the same
                    # deterministic samplers as extract_json so the startup
                    # "client effective sampling" log is accurate for EVERY
                    # LLMClient request path, not just JSON extraction.  top_p
                    # and seed are native OpenAI params; top_k and min_p are not,
                    # so they ride in extra_body, which only self-hosted
                    # OpenAI-compatible backends (llama-server, vLLM) accept.
                    # Cloud providers reject unknown extra_body params (HTTP
                    # 400), so top_k/min_p are omitted there.
                    if self.top_p is not None:
                        kwargs["top_p"] = self.top_p
                    if self.seed is not None:
                        kwargs["seed"] = self.seed
                    sampler_extra: dict = {}
                    if not self._is_cloud_provider:
                        if self.top_k is not None:
                            sampler_extra["top_k"] = self.top_k
                        if self.min_p is not None:
                            sampler_extra["min_p"] = self.min_p
                    if timeout is not None:
                        kwargs["timeout"] = timeout
                    if self._is_ollama:
                        # Note: top_k/min_p are not forwarded on the Ollama path
                        # (Ollama uses its own options mapping); only the
                        # llama-server/OpenAI path threads them via extra_body.
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
                    elif sampler_extra:
                        kwargs["extra_body"] = sampler_extra

                    hard_deadline = (timeout or self.default_timeout) * 3
                    response = self._call_with_deadline(
                        lambda: self._next_client().chat.completions.create(**kwargs),
                        hard_deadline,
                    )
                    raw_text = response.choices[0].message.content

                # One raw-IO capture record per LLM call (#501 finding 1): tee
                # BEFORE the empty-response check so failed/retried calls are
                # captured too.  Real backend token counts are used; skipped at
                # zero cost when capture is disabled.
                if capture is not None and self._raw_io_capture_path is not None:
                    self._write_raw_io_record(
                        capture, messages, raw_text or "", response,
                        output_tokens=stream_out_tokens,
                        input_tokens=stream_in_tokens,
                    )

                if not raw_text:
                    raise LLMExtractionError("Empty response from LLM.")

                self.stats.record_success()
                return strip_thinking_blocks(raw_text.strip())

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

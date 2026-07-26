"""LLM API handler with rate limiting and round-robin key rotation.

Migrated from the original project's ``llm_handler.py`` with:
- Constructor injection of ``ModelConfig`` instead of importing hardcoded config.
- Retriable vs non-retriable error classification so the pipeline can
  decide whether to retry a failed API call or give up immediately.
- Multiple fallback parsing strategies for non-JSON responses (via
  ``clean_json_string`` in ``json_utils``).

Architecture:
  ``LLMHandler`` manages one or more ``OpenAI`` client instances (one per
  API key). Clients are selected in round-robin order for each request.
  Before each request, the ``RateLimiter`` checks a sliding-window counter
  and blocks if the request quota has been exceeded for the current window.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from openai import OpenAI, APIError, RateLimitError, APIConnectionError
from tqdm import tqdm

from .models import ModelConfig


class RateLimiter:
    """Sliding-window rate limiter for API requests.

    Tracks request timestamps in a deque. When ``wait_if_needed()`` is
    called, expired timestamps (older than ``window_seconds``) are evicted,
    and if the remaining count equals ``max_requests`` the caller is blocked
    until the oldest timestamp expires.

    Thread-safe via ``self.lock``.

    Attributes:
        max_requests: Max requests allowed in the sliding window. ``0`` or
            negative means no limit.
        window_seconds: Length of the sliding window in seconds.
        requests: Deque of ``time.time()`` timestamps for recent requests.
        lock: Guards all access to ``self.requests``.

    Usage:
        limiter = RateLimiter(max_requests=10, window_seconds=60)
        limiter.wait_if_needed()  # blocks until a slot is free
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        """Initialise the rate limiter.

        Args:
            max_requests: Maximum number of requests per sliding window.
                Pass ``0`` or a negative value to disable rate limiting.
            window_seconds: Duration of the sliding window in seconds.
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: deque = deque()
        self.lock = threading.Lock()

    def wait_if_needed(self) -> None:
        """Block until a request slot is available, or return immediately.

        If ``max_requests <= 0``, rate limiting is disabled and this is a
        no-op.

        The method:

        1. Evicts timestamps older than ``now - window_seconds``.
        2. If the deque length is still at ``max_requests``, calculates
           how long until the oldest timestamp expires.
        3. Sleeps for that duration (outside the lock so other threads can
           check in parallel), then retries.

        This design means multiple waiters may briefly exceed the limit,
        but on average the rate is respected.
        """
        if self.max_requests <= 0:
            return

        while True:
            sleep_time = 0.0
            with self.lock:
                now = time.time()
                # Remove expired entries (those older than the window)
                while self.requests and self.requests[0] <= now - self.window_seconds:
                    self.requests.popleft()

                if len(self.requests) < self.max_requests:
                    # Slot available — register this request and proceed
                    self.requests.append(now)
                    return

                # No slot yet: compute how long until the oldest slot expires
                sleep_time = self.requests[0] + self.window_seconds - now

            if sleep_time > 0:
                tqdm.write(
                    f"[*] Rate limit reached ({self.max_requests}/{self.window_seconds}s). "
                    f"Waiting {sleep_time:.1f}s..."
                )
                time.sleep(sleep_time)
            else:
                # Yield to other threads when clock granularity produces 0 sleep
                time.sleep(0.01)


class LLMHandler:
    """Handles LLM API requests with rate limiting and round-robin API key rotation.

    One ``OpenAI`` client is created per API key. Each call to
    ``get_client()`` advances the key index so successive requests are spread
    across keys. The rate limiter is shared across all keys (i.e. the global
    request rate is limited, not per-key).

    The default ``User-Agent`` header is overridden because many proxy APIs
    block the standard ``openai-python`` user-agent string.

    Attributes:
        model_config: The resolved ``ModelConfig`` for the target model.
        keys: List of API key strings resolved from environment variables.
        current_key_index: Index for round-robin key selection.
        clients: One ``OpenAI`` client per API key.
        limiter: Sliding-window rate limiter.
        lock: Guards ``current_key_index`` for thread-safe round-robin.

    Usage:
        handler = LLMHandler(model_config)
        response = handler.request("You are an expert...", "Extract data from...")
    """

    def __init__(self, model_config: ModelConfig) -> None:
        """Initialise API clients and the rate limiter.

        Resolves API keys from the environment variable named in
        ``model_config.api_key_env``, creates one ``OpenAI`` client per key,
        and sets up the rate limiter with the configured thresholds.

        Args:
            model_config: Configuration object specifying ``api_key_env``,
                ``api_base``, ``model_name``, and ``rate_limit`` settings.

        Raises:
            ValueError: If the configured environment variable is empty or
                not set.
        """
        self.model_config = model_config

        # Resolve API keys from environment (comma-separated for multiple keys)
        raw_key = os.environ.get(model_config.api_key_env, "")
        self.keys: List[str] = [k.strip() for k in raw_key.split(",") if k.strip()]

        if not self.keys:
            raise ValueError(
                f"API key not found in environment variable '{model_config.api_key_env}' "
                f"(configured for model '{model_config.alias}')"
            )

        self.current_key_index: int = 0
        self.limiter = RateLimiter(
            model_config.rate_limit.max_requests,
            model_config.rate_limit.window_seconds,
        )
        # Many proxy APIs block the default openai-python User-Agent.
        # Spoof a common browser UA to avoid being rejected pre-emptively.
        self.default_headers: Dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        self.clients: List[OpenAI] = [
            OpenAI(
                api_key=key,
                base_url=model_config.api_base,
                default_headers=self.default_headers,
                timeout=120.0,  # Fail-fast on hung endpoints
            )
            for key in self.keys
        ]
        self.lock = threading.Lock()

    def close(self) -> None:
        """Close all OpenAI client connections to free TCP resources."""
        for client in self.clients:
            client.close()

    def get_client(self) -> OpenAI:
        """Get the next client in round-robin order.

        Each call advances ``current_key_index``, wrapping around when it
        reaches the end of the list. This distributes requests evenly across
        all available API keys.

        Returns:
            The next ``OpenAI`` client instance.
        """
        with self.lock:
            client = self.clients[self.current_key_index]
            self.current_key_index = (self.current_key_index + 1) % len(self.clients)
            return client

    def is_retriable(self, error: Exception) -> bool:
        """Classify whether an error is safe to retry.

        Retriable errors are those that may succeed on a subsequent attempt:
        - ``RateLimitError`` (HTTP 429) — backing off may help.
        - ``APIConnectionError`` — temporary network issues.
        - ``APIError`` with status code 429 or 5xx (server-side errors).
        - ``ConnectionError`` / ``TimeoutError`` — network-level failures.

        Non-retriable errors include auth failures (HTTP 401/403),
        invalid requests (HTTP 400), and context-length exceeded errors,
        which will not succeed no matter how many times they are retried.

        Args:
            error: The exception to classify.

        Returns:
            ``True`` if the error is retriable, ``False`` if it is permanent.
        """
        if isinstance(error, (RateLimitError, APIConnectionError)):
            return True
        if isinstance(error, APIError):
            # 429 = rate limit, 5xx = server errors
            status = getattr(error, "status_code", 0)
            return status in (429,) or (500 <= status < 600)
        # Network/timeout errors are retriable
        if isinstance(error, (ConnectionError, TimeoutError)):
            return True
        return False

    def request(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[Dict[str, Any]] = None,
        image_parts: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Make a single API request with rate limiting and key rotation.

        The method:

        1. Blocks at the rate limiter until a request slot is available.
        2. Selects the next API client (round-robin).
        3. Calls ``client.chat.completions.create()``.
        4. Returns the raw response text.

        When *image_parts* is provided (a list of ``{"type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,..."}}`` dicts), the
        user message is sent as a multimodal content array with the text
        prompt first followed by the image parts.  When *image_parts* is
        ``None`` (the default), the user message is sent as a plain string.

        ``response_format`` is accepted for API compatibility but is **not**
        passed through to the client, because many Claude-compatible proxy
        servers reject the ``response_format`` parameter that OpenAI's SDK
        sends. The prompt template itself is expected to instruct JSON output.

        Args:
            system_prompt: The system message content.
            user_prompt: The user message content (text).
            response_format: Optional OpenAI-style response format dict
                (currently unused — see note above).
            image_parts: Optional list of image content parts for multimodal
                vision requests.

        Returns:
            The raw response text from the LLM's first choice.

        Raises:
            Exception: Re-raises any API exception after logging. The caller
                (``_process_task`` in ``pipeline.py``) is responsible for
                checking ``is_retriable()``.
        """
        # Rate limiting wait
        self.limiter.wait_if_needed()

        client = self.get_client()
        try:
            # Strip response_format for Claude-compatible proxies that don't
            # support it. The prompt template already instructs JSON output.
            kwargs: Dict[str, Any] = {
                "model": self.model_config.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                ],
            }

            # Build user message: plain text or multimodal content array
            if image_parts:
                kwargs["messages"].append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        *image_parts,
                    ],
                })
            else:
                kwargs["messages"].append({
                    "role": "user",
                    "content": user_prompt,
                })

            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            tqdm.write(f"[*] API Request failed: {e}")
            raise e
"""Utilities for estimating prompt and completion token counts."""
from __future__ import annotations

import re
from typing import Iterable, Dict, Any

try:
    import tiktoken  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    tiktoken = None  # type: ignore

_DEFAULT_ENCODING = "cl100k_base"
_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _estimate_with_tiktoken(text: str, model: str) -> int | None:
    if not tiktoken:
        return None

    encoding = None
    if model:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = None
        except Exception:
            encoding = None
    if encoding is None:
        try:
            encoding = tiktoken.get_encoding(_DEFAULT_ENCODING)
        except Exception:
            return None

    try:
        return len(encoding.encode(text))
    except Exception:
        return None


def estimate_tokens_for_text(text: str, model: str = "") -> int:
    """Estimate the token count for a block of text.

    Attempts to use ``tiktoken`` when available for accurate counts on OpenAI
    compatible models. Falls back to a lightweight heuristic otherwise.
    """

    if not text:
        return 0

    normalized_text = text.strip()
    if not normalized_text:
        return 0

    tiktoken_estimate = _estimate_with_tiktoken(normalized_text, model)
    if tiktoken_estimate is not None:
        return tiktoken_estimate

    heuristic_tokens = _TOKEN_PATTERN.findall(normalized_text)
    if heuristic_tokens:
        return len(heuristic_tokens)

    # Final safety net: assume roughly 4 characters per token as a heuristic.
    return max(1, len(normalized_text) // 4)


def estimate_tokens_for_messages(messages: Iterable[Dict[str, Any]], model: str = "") -> int:
    """Estimate token usage for a list of OpenAI-style messages."""

    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens_for_text(content, model)
    return total

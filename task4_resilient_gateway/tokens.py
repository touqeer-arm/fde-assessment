import json
from functools import lru_cache
from typing import Any

import tiktoken

ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _get_encoding() -> tiktoken.Encoding:
    """Load and cache the tokenizer on first use."""
    return tiktoken.get_encoding(ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Return the exact token count for the configured encoding."""
    return len(_get_encoding().encode(text))


def count_request_tokens(
    payload: dict[str, Any],
) -> int:
    """Count tokens in a deterministic JSON representation of the request."""
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    return count_tokens(serialized)

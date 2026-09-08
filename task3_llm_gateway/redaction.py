import re

# Detect complete PII values once enough streamed text has arrived.
_PII_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z])"
    r"|\b\d{3}-\d{2}-\d{4}\b"
    r"|(?<!\d)(?<!\d[ \-])(?:\d[ \-]?){12,18}\d(?![ \-]?\d)"
)

# Characters that may form an email candidate at the end of a chunk.
_EMAIL_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-@"
)

# Characters that may form an SSN or credit-card candidate.
_DIGIT_RUN_CHARS = frozenset("0123456789 -")

# Hard bound on ambiguous state retained between stream chunks.
MAX_PENDING_CHARS = 320


def redact_pii(text: str) -> str:
    """Replace complete supported PII values with a redaction marker."""
    return _PII_PATTERN.sub("[REDACTED]", text)


class StreamingRedactor:
    """Redact PII incrementally without buffering the full response."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> str:
        # Combine only the previously ambiguous suffix with the new delta.
        buffer = redact_pii(self.buffer + text)

        pending_start = self._commit_point(buffer)

        output = buffer[:pending_start]
        pending = buffer[pending_start:]

        # Security guardrails should fail closed. If an ambiguous candidate
        # grows beyond our bounded state, redact it instead of leaking part
        # of something that could later become PII.
        if len(pending) > MAX_PENDING_CHARS:
            output += "[REDACTED]"
            pending = ""

        self.buffer = pending

        return output

    def flush(self) -> str:
        """Flush remaining buffered text when the stream completes."""
        output = redact_pii(self.buffer)
        self.buffer = ""
        return output

    @staticmethod
    def _commit_point(buffer: str) -> int:
        """Return the index up to which streamed text is safe to emit."""
        length = len(buffer)

        # Hold a trailing sequence that could still grow into an email.
        email_start = length

        while email_start > 0 and buffer[email_start - 1] in _EMAIL_CHARS:
            email_start -= 1

        if not any(character.isalnum() for character in buffer[email_start:]):
            email_start = length

        # Hold a trailing numeric sequence that could still grow into an
        # SSN or credit-card number.
        digits_start = length

        while digits_start > 0 and buffer[digits_start - 1] in _DIGIT_RUN_CHARS:
            digits_start -= 1

        while digits_start < length and not buffer[digits_start].isdigit():
            digits_start += 1

        return min(email_start, digits_start)

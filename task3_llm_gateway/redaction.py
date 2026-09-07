import re

EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

SSN_PATTERN = re.compile(
    r"\b\d{3}-\d{2}-\d{4}\b"
)

CREDIT_CARD_PATTERN = re.compile(
    r"(?<![\d -])(?:\d[ -]?){12,18}\d(?![\d -])"
)

EMAIL_CANDIDATE = re.compile(
    r"[A-Za-z0-9._%+@-]+$"
)

NUMBER_CANDIDATE = re.compile(
    r"\d[\d -]*$"
)

MAX_PENDING_CHARS = 320


def redact_pii(text: str) -> str:
    text = EMAIL_PATTERN.sub("[REDACTED]", text)
    text = SSN_PATTERN.sub("[REDACTED]", text)
    return CREDIT_CARD_PATTERN.sub("[REDACTED]", text)


class StreamingRedactor:
    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> str:
        self.buffer += text

        pending_start = self._pending_start()

        safe_text = self.buffer[:pending_start]
        self.buffer = self.buffer[pending_start:]

        output = redact_pii(safe_text)

        if len(self.buffer) > MAX_PENDING_CHARS:
            output += "[REDACTED]"
            self.buffer = ""

        return output

    def flush(self) -> str:
        output = redact_pii(self.buffer)
        self.buffer = ""
        return output

    def _pending_start(self) -> int:
        starts = []

        email_match = EMAIL_CANDIDATE.search(self.buffer)

        if (
            email_match is not None
            and any(
                character.isalnum()
                for character in email_match.group()
            )
        ):
            starts.append(email_match.start())

        number_match = NUMBER_CANDIDATE.search(self.buffer)

        if number_match is not None:
            starts.append(number_match.start())

        if not starts:
            return len(self.buffer)

        return min(starts)
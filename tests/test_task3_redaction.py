from task3_llm_gateway.redaction import (
    MAX_PENDING_CHARS,
    StreamingRedactor,
)


def run_stream(chunks: list[str]) -> str:
    redactor = StreamingRedactor()

    output = ""

    for chunk in chunks:
        output += redactor.feed(chunk)

    output += redactor.flush()

    return output


def test_email_is_redacted_across_chunks():
    result = run_stream(
        [
            "Contact john.",
            "doe@example.com for help.",
        ]
    )

    assert result == "Contact [REDACTED] for help."
    assert "john.doe@example.com" not in result


def test_ssn_is_redacted_across_chunks():
    result = run_stream(
        [
            "SSN: 123-45-",
            "6789.",
        ]
    )

    assert result == "SSN: [REDACTED]."
    assert "123-45-6789" not in result


def test_credit_card_is_redacted_across_chunks():
    result = run_stream(
        [
            "Card: 4111 1111 ",
            "1111 1111.",
        ]
    )

    assert result == "Card: [REDACTED]."
    assert "4111 1111 1111 1111" not in result


def test_multiple_pii_types_are_redacted():
    result = run_stream(
        [
            "Email john.",
            "doe@example.com SSN 123-45-",
            "6789 Card 4111 1111 ",
            "1111 1111.",
        ]
    )

    assert "john.doe@example.com" not in result
    assert "123-45-6789" not in result
    assert "4111 1111 1111 1111" not in result

    assert result.count("[REDACTED]") == 3


def test_clean_text_is_preserved():
    result = run_stream(
        [
            "Hello ",
            "from the ",
            "streaming gateway.",
        ]
    )

    assert result == "Hello from the streaming gateway."


def test_safe_completed_text_is_emitted_without_waiting_for_full_stream():
    redactor = StreamingRedactor()

    output = redactor.feed("This text is safe ")

    assert output == "This text is safe "


def test_pending_buffer_is_bounded():
    redactor = StreamingRedactor()

    redactor.feed("a" * (MAX_PENDING_CHARS + 100))

    assert len(redactor.buffer) <= MAX_PENDING_CHARS


def test_oversized_ambiguous_candidate_fails_closed():
    redactor = StreamingRedactor()

    output = redactor.feed("a" * (MAX_PENDING_CHARS + 1))

    assert output == "[REDACTED]"
    assert redactor.buffer == ""


def test_credit_card_split_across_three_chunks_is_redacted():
    result = run_stream(
        [
            "4111 ",
            "1111 1111 ",
            "1111 now",
        ]
    )

    assert "4111 1111 1111 1111" not in result
    assert result == "[REDACTED] now"


def test_number_not_part_of_pii_is_preserved():
    result = run_stream(
        [
            "I have ",
            "3 cats and ",
            "42 dogs.",
        ]
    )

    assert result == "I have 3 cats and 42 dogs."

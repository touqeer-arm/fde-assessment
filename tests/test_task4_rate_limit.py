import asyncio

import pytest

from task4_resilient_gateway.rate_limit import SlidingWindowRateLimiter


@pytest.mark.asyncio
async def test_exact_50000_token_limit_is_allowed(tmp_path):
    limiter = SlidingWindowRateLimiter(
        tmp_path / "usage.db"
    )
    await limiter.initialize()

    result = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=50_000,
        now=1000.0,
    )

    assert result.allowed is True
    assert result.used_tokens == 50_000

    blocked = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=1,
        now=1000.1,
    )

    assert blocked.allowed is False


@pytest.mark.asyncio
async def test_tenants_have_independent_limits(tmp_path):
    limiter = SlidingWindowRateLimiter(
        tmp_path / "usage.db"
    )
    await limiter.initialize()

    tenant_a = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=50_000,
        now=1000.0,
    )

    tenant_b = await limiter.check_and_record(
        tenant_id="tenant-b",
        tokens=50_000,
        now=1000.0,
    )

    assert tenant_a.allowed is True
    assert tenant_b.allowed is True


@pytest.mark.asyncio
async def test_tokens_expire_from_sliding_window(tmp_path):
    limiter = SlidingWindowRateLimiter(
        tmp_path / "usage.db"
    )
    await limiter.initialize()

    first = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=40_000,
        now=1000.0,
    )

    second = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=10_000,
        now=1059.0,
    )

    blocked = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=1,
        now=1059.5,
    )

    after_expiry = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=1,
        now=1060.1,
    )

    assert first.allowed is True
    assert second.allowed is True
    assert blocked.allowed is False
    assert after_expiry.allowed is True
    assert after_expiry.used_tokens == 10_001


@pytest.mark.asyncio
async def test_usage_is_persisted_in_sqlite(tmp_path):
    database_path = tmp_path / "usage.db"

    first_limiter = SlidingWindowRateLimiter(
        database_path
    )
    await first_limiter.initialize()

    await first_limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=20_000,
        now=1000.0,
    )

    second_limiter = SlidingWindowRateLimiter(
        database_path
    )
    await second_limiter.initialize()

    result = await second_limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=30_000,
        now=1001.0,
    )

    assert result.allowed is True
    assert result.used_tokens == 50_000


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_overspend_limit(
    tmp_path,
):
    limiter = SlidingWindowRateLimiter(
        tmp_path / "usage.db"
    )
    await limiter.initialize()

    results = await asyncio.gather(
        limiter.check_and_record(
            tenant_id="tenant-a",
            tokens=30_000,
            now=1000.0,
        ),
        limiter.check_and_record(
            tenant_id="tenant-a",
            tokens=30_000,
            now=1000.0,
        ),
    )

    allowed_results = [
        result.allowed
        for result in results
    ]

    assert allowed_results.count(True) == 1
    assert allowed_results.count(False) == 1


@pytest.mark.asyncio
async def test_retry_after_waits_until_enough_tokens_expire(
    tmp_path,
):
    limiter = SlidingWindowRateLimiter(
        tmp_path / "usage.db"
    )
    await limiter.initialize()

    await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=1_000,
        now=1000.0,
    )

    await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=49_000,
        now=1030.0,
    )

    blocked = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=5_000,
        now=1050.0,
    )

    assert blocked.allowed is False

    # At t=1060 only 1,000 tokens expire, which is not enough.
    # At t=1090 the 49,000-token record also expires.
    assert blocked.retry_after == 40


@pytest.mark.asyncio
async def test_request_larger_than_limit_cannot_become_allowed(
    tmp_path,
):
    limiter = SlidingWindowRateLimiter(
        tmp_path / "usage.db"
    )
    await limiter.initialize()

    result = await limiter.check_and_record(
        tenant_id="tenant-a",
        tokens=50_001,
        now=1000.0,
    )

    assert result.allowed is False
    assert result.retry_after is None

@pytest.mark.asyncio
async def test_independent_limiters_cannot_overspend_shared_database(
    tmp_path,
):
    database_path = tmp_path / "usage.db"

    first_limiter = SlidingWindowRateLimiter(
        database_path
    )
    second_limiter = SlidingWindowRateLimiter(
        database_path
    )

    await first_limiter.initialize()
    await second_limiter.initialize()

    results = await asyncio.gather(
        first_limiter.check_and_record(
            tenant_id="tenant-a",
            tokens=30_000,
            now=1000.0,
        ),
        second_limiter.check_and_record(
            tenant_id="tenant-a",
            tokens=30_000,
            now=1000.0,
        ),
    )

    allowed_results = [
        result.allowed
        for result in results
    ]

    assert allowed_results.count(True) == 1
    assert allowed_results.count(False) == 1
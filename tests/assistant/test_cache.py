"""``ResponseCache`` — the request-keyed cache serving both live dependencies
(T035a, packet P1a).

Covers exactly what the packet's exit criterion names: cache round-trip, canary
divergence, and an unfillable miss. Key order never matters for either the
main request or the canary — canonicalization is `ResponseCache`'s own job — so
one test builds a request with reordered keys on purpose to pin that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from water_assistant_agent.assistant.cache import (
    Canary,
    CacheMissError,
    CanaryMismatchError,
    ResponseCache,
)


@pytest.fixture
def cache(tmp_path: Path) -> ResponseCache:
    return ResponseCache(tmp_path / "cache")


@pytest.mark.asyncio
async def test_round_trip_hits_without_a_live_call(cache: ResponseCache) -> None:
    request = {"url": "https://x", "params": {"b": 1, "a": 2}}
    calls = 0

    async def live_fetch() -> dict:
        nonlocal calls
        calls += 1
        return {"data": [1, 2, 3]}

    first = await cache.fetch(request, live_fetch)
    second = await cache.fetch(request, live_fetch)

    assert first == second == {"data": [1, 2, 3]}
    assert calls == 1, "the second fetch must be served from the committed entry"


@pytest.mark.asyncio
async def test_round_trip_key_order_never_matters(cache: ResponseCache) -> None:
    """The same request built with keys in a different order still hits."""
    a = {"url": "https://x", "params": {"b": 1, "a": 2}}
    b = {"params": {"a": 2, "b": 1}, "url": "https://x"}

    async def live_fetch() -> dict:
        return {"data": "v1"}

    await cache.fetch(a, live_fetch)
    assert cache.get(b) == {"data": "v1"}


@pytest.mark.asyncio
async def test_unfillable_miss_when_live_is_disabled(cache: ResponseCache) -> None:
    request = {"url": "https://y"}

    async def live_fetch() -> dict:
        raise AssertionError("must not be called when allow_live=False")

    with pytest.raises(CacheMissError) as exc_info:
        await cache.fetch(request, live_fetch, allow_live=False)

    assert exc_info.value.canonical_request == request


@pytest.mark.asyncio
async def test_unfillable_miss_when_the_live_fetch_raises(cache: ResponseCache) -> None:
    captured = {"url": "https://captured"}

    async def captured_fetch() -> dict:
        return {"ok": True}

    await cache.fetch(captured, captured_fetch)

    failing_request = {"url": "https://failing"}

    async def failing_fetch() -> dict:
        raise RuntimeError("service down")

    with pytest.raises(CacheMissError) as exc_info:
        await cache.fetch(failing_request, failing_fetch)

    assert exc_info.value.canonical_request == failing_request
    # A prior entry exists, so the failure is annotated with the nearest one.
    assert exc_info.value.nearest_request == captured
    assert exc_info.value.diff


@pytest.mark.asyncio
async def test_unfillable_miss_with_an_empty_cache_names_no_nearest_request(
    cache: ResponseCache,
) -> None:
    async def failing_fetch() -> dict:
        raise RuntimeError("service down")

    with pytest.raises(CacheMissError) as exc_info:
        await cache.fetch({"url": "https://z"}, failing_fetch)

    assert exc_info.value.nearest_request is None
    assert exc_info.value.diff is None


@pytest.mark.asyncio
async def test_canary_first_capture_records_and_serves_the_real_request(
    cache: ResponseCache,
) -> None:
    canary_request = {"probe": "gr2l-canary"}

    async def canary_fetch() -> dict:
        return {"v": 1}

    async def live_fetch() -> dict:
        return {"data": "real"}

    canary = Canary(canary_request, canary_fetch)
    result = await cache.fetch({"url": "https://real"}, live_fetch, canary=canary)

    assert result == {"data": "real"}
    assert cache.get(canary_request) == {"v": 1}, "the canary's own first capture is committed"


@pytest.mark.asyncio
async def test_canary_divergence_blocks_the_new_entry(cache: ResponseCache) -> None:
    canary_request = {"probe": "gr2l-canary"}

    async def canary_v1() -> dict:
        return {"v": 1}

    async def first_live_fetch() -> dict:
        return {"data": "first"}

    await cache.fetch({"url": "https://first"}, first_live_fetch, canary=Canary(canary_request, canary_v1))

    async def canary_v2() -> dict:
        return {"v": 2}

    real_request = {"url": "https://second"}

    async def live_fetch() -> dict:
        raise AssertionError("must not be called once the canary has diverged")

    with pytest.raises(CanaryMismatchError) as exc_info:
        await cache.fetch(real_request, live_fetch, canary=Canary(canary_request, canary_v2))

    assert exc_info.value.committed_response == {"v": 1}
    assert exc_info.value.live_response == {"v": 2}
    assert cache.get(real_request) is None, "no entry may record from an unverified service"


@pytest.mark.asyncio
async def test_canary_never_consulted_on_a_hit(cache: ResponseCache) -> None:
    """A hit skips the canary entirely — no verification call, however it would answer."""
    request = {"url": "https://cached"}

    async def live_fetch() -> dict:
        return {"data": "v"}

    await cache.fetch(request, live_fetch)

    async def canary_that_must_not_run() -> dict:
        raise AssertionError("canary must not be consulted on a hit")

    result = await cache.fetch(
        request,
        live_fetch,
        canary=Canary({"probe": "x"}, canary_that_must_not_run),
    )
    assert result == {"data": "v"}

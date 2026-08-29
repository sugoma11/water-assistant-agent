"""``ResponseCache`` — the request-keyed cache serving both live dependencies
(T035a, packet P1a).

Covers exactly what the packet's exit criterion names: cache round-trip, canary
divergence, and an unfillable miss. Key order never matters for either the
main request or the canary — canonicalization is `ResponseCache`'s own job — so
one test builds a request with reordered keys on purpose to pin that.

`fetch_many` was added by T146 for a caller that misses in runs rather than one
at a time. What its own tests are for is that batching the *fill* changes nothing
about the *policy*: the same allow_live gate, the same hard failure, the same one
committed entry per request. The two ways it could go wrong are both alignment —
a response landing against the wrong request, or a fill answering fewer requests
than it was handed — and both are here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


# ── fetch_many: one policy, a caller that may batch its fills (T146) ──────────


@pytest.mark.asyncio
async def test_fetch_many_returns_in_the_order_asked(cache: ResponseCache) -> None:
    """Hits and newly recorded misses come back interleaved in the caller's order.

    The weather client assembles a window by position, so a response landing
    against the wrong request would shift a day rather than fail.
    """
    requests = [{"day": day} for day in ("d1", "d2", "d3")]
    await cache.fetch({"day": "d2"}, _returning({"v": "held"}))

    async def live_fill(missing: list[dict]) -> list[dict]:
        return [{"v": request["day"]} for request in missing]

    assert await cache.fetch_many(requests, live_fill) == [
        {"v": "d1"},
        {"v": "held"},
        {"v": "d3"},
    ]


@pytest.mark.asyncio
async def test_fetch_many_asks_for_every_miss_at_once(cache: ResponseCache) -> None:
    """One call, however many misses — which is the whole reason the method exists."""
    calls: list[list[dict]] = []

    async def live_fill(missing: list[dict]) -> list[dict]:
        calls.append(missing)
        return [{"v": request["day"]} for request in missing]

    await cache.fetch_many([{"day": f"d{i}"} for i in range(5)], live_fill)

    assert len(calls) == 1
    assert [request["day"] for request in calls[0]] == ["d0", "d1", "d2", "d3", "d4"]


@pytest.mark.asyncio
async def test_fetch_many_commits_each_filled_request_under_its_own_key(
    cache: ResponseCache,
) -> None:
    """A batched fill still lands as individual entries, reusable one at a time."""

    async def live_fill(missing: list[dict]) -> list[dict]:
        return [{"v": request["day"]} for request in missing]

    await cache.fetch_many([{"day": "d1"}, {"day": "d2"}], live_fill)

    assert cache.get({"day": "d1"}) == {"v": "d1"}
    assert cache.get({"day": "d2"}) == {"v": "d2"}


@pytest.mark.asyncio
async def test_fetch_many_hits_never_reach_the_fill(cache: ResponseCache) -> None:
    await cache.fetch({"day": "d1"}, _returning({"v": "held"}))

    async def live_fill(missing: list[dict]) -> list[dict]:
        raise AssertionError("a fully held batch must not be filled")

    assert await cache.fetch_many([{"day": "d1"}], live_fill) == [{"v": "held"}]


@pytest.mark.asyncio
async def test_fetch_many_refuses_a_miss_when_live_is_disabled(
    cache: ResponseCache,
) -> None:
    async def live_fill(missing: list[dict]) -> list[dict]:
        raise AssertionError("must not be called when allow_live=False")

    with pytest.raises(CacheMissError) as raised:
        await cache.fetch_many([{"day": "d1"}, {"day": "d2"}], live_fill, allow_live=False)

    assert raised.value.canonical_request == {"day": "d1"}


@pytest.mark.asyncio
async def test_fetch_many_refuses_a_mis_sized_fill(cache: ResponseCache) -> None:
    """A fill answering fewer requests than it was given would mis-align the rest.

    Silently zipping the two would pair the second request with the third
    request's response, which is a wrong answer rather than a missing one — the
    failure this cache exists to make impossible.
    """

    async def short_fill(missing: list[dict]) -> list[dict]:
        return [{"v": "only one"}]

    with pytest.raises(CacheMissError, match="answered 1 of 2"):
        await cache.fetch_many([{"day": "d1"}, {"day": "d2"}], short_fill)

    assert cache.get({"day": "d1"}) is None, "a refused fill commits nothing"


def _returning(response: dict) -> Any:
    async def live_fetch() -> dict:
        return response

    return live_fetch


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

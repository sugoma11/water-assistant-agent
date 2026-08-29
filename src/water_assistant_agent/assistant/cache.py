"""``ResponseCache`` — one request-keyed cache serving both live dependencies.

Open-Meteo (weather) and GR2L (the green-roof model service) are the testbed's
two live HTTP dependencies. Both are served through this one mechanism rather
than two, so there is one replay path to keep honest instead of two, and one
request hash format instead of a second artefact (a fixture file) that could
drift from what the client actually sends. See ``agent_architecture.md`` §5 and
``decisions.md`` § The response cache.

Entries are committed JSON under ``eval/cache/``, one file per request, keyed by
the sha256 of a **canonical request** the caller builds: URL plus the full query
parameter mapping for Open-Meteo, ``data[]`` plus the model parameters for GR2L.
Key order never matters — ``key_for`` canonicalizes via ``sort_keys=True`` — so
callers pass whatever mapping they already have.

**The two callers no longer key at the same granularity, and :meth:`fetch_many`
is why this module knows about it** (T146). GR2L is keyed on the request it
sends, the whole fetched row array included, because that request *is* the thing
whose determinism the cache carries. Weather is keyed one **calendar day** at a
time — the canonical one-day Archive request — and a window is assembled from
days, so the set of keys a rollout can reach is the days around its ``as_of``
rather than the plane of windows over them. A day-keyed caller misses in runs
rather than one at a time and would issue one request per missing day if it went
through :meth:`fetch` in a loop, so it hands the whole missing list to
:meth:`fetch_many` and fills it in as few requests as it likes; the hit/miss
policy — the ``allow_live`` gate, the hard failure, the recording — stays here
either way.

A cache miss is filled live and recorded, **gated on a canary matching**: before
any new entry is written, the caller's canary request is re-fetched and compared
byte-for-byte against the canary response already committed, so no entry ever
enters the cache from a service run that has not just been verified. A diverging
canary and a miss nothing can fill (live fetch disabled, or the fetch itself
fails) are both hard failures, carrying the unmatched request and a diff against
the nearest already-captured request for diagnosis.
"""

import dataclasses
import difflib
import hashlib
import json
import os
import threading
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _canonical_json(value: Any) -> str:
    """Stable JSON text for *value* — sorted keys, no whitespace ambiguity."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class CacheMissError(RuntimeError):
    """A cache miss could not be filled — a hard failure, never a silent live call.

    Raised when live fetching is disabled or the live fetch itself fails. Carries
    the unmatched request and, when the cache holds any entries at all, the single
    nearest committed request and a unified diff against it, so a missed capture
    is diagnosable without re-deriving what was actually recorded.
    """

    def __init__(
        self,
        canonical_request: Any,
        nearest: tuple[Any, str] | None,
        *,
        reason: str,
    ) -> None:
        self.canonical_request = canonical_request
        self.nearest_request = None if nearest is None else nearest[0]
        self.diff = None if nearest is None else nearest[1]
        message = f"Unfillable cache miss ({reason}): {_canonical_json(canonical_request)}"
        if self.diff:
            message += f"\nNearest captured request differs:\n{self.diff}"
        super().__init__(message)


class CanaryMismatchError(RuntimeError):
    """The service's current response to the canary no longer matches the committed one.

    The response cache's determinism guarantee for this service rests on the
    canary staying stable; a mismatch means the service has moved under the
    pinned contract, and no new entry may be recorded until that is resolved.
    """

    def __init__(self, canary_request: Any, committed_response: Any, live_response: Any) -> None:
        self.canary_request = canary_request
        self.committed_response = committed_response
        self.live_response = live_response
        diff = "\n".join(
            difflib.unified_diff(
                _canonical_json(committed_response).splitlines(),
                _canonical_json(live_response).splitlines(),
                fromfile="committed",
                tofile="live",
                lineterm="",
            )
        )
        super().__init__(
            f"Canary diverged for request {_canonical_json(canary_request)}:\n{diff}"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Canary:
    """A committed request/live-fetch pair proving the service hasn't moved.

    ``request`` is the same canonical-request shape any other cached request
    uses, so its first capture is recorded exactly like any other entry; every
    later fill re-fetches it via ``live_fetch`` and compares the result against
    what is already committed.
    """

    request: Any
    live_fetch: Callable[[], Awaitable[Any]]


class ResponseCache:
    """Committed-JSON response cache, one file per canonical request under *cache_dir*."""

    def __init__(self, cache_dir: Path | str) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key_for(canonical_request: Any) -> str:
        """sha256 of *canonical_request*'s canonical JSON text."""
        return hashlib.sha256(_canonical_json(canonical_request).encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def get(self, canonical_request: Any) -> Any | None:
        """The committed response for *canonical_request*, or ``None`` on a miss."""
        path = self._path(self.key_for(canonical_request))
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["response"]

    def put(self, canonical_request: Any, response: Any) -> None:
        """Commit *response* for *canonical_request*, overwriting any existing entry.

        Written to a sibling temporary file and moved into place, because since
        T146 the measurement path records too and runs its rollouts in threads.
        Two of them filling the same weather day write the same bytes, so the
        race is not over *what* lands — it is that a truncate-then-write leaves a
        window in which a third thread reads half a file, and a half-read entry
        surfaces as a decode error on a case that had a perfectly good hit.
        ``os.replace`` is atomic within a directory, so a reader sees either the
        old entry or the new one.
        """
        key = self.key_for(canonical_request)
        entry = {"request": canonical_request, "response": response}
        path = self._path(key)
        pending = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        pending.write_text(
            json.dumps(entry, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(pending, path)

    def nearest(self, canonical_request: Any) -> tuple[Any, str] | None:
        """The committed request closest to *canonical_request* by text similarity.

        Returns ``(that request, a unified diff against it)``, or ``None`` when the
        cache holds no entries at all. Used only to annotate a miss — never consulted
        on the hit path.
        """
        target_text = _canonical_json(canonical_request)
        best: tuple[float, Any, str] | None = None
        for path in self._cache_dir.glob("*.json"):
            entry = json.loads(path.read_text(encoding="utf-8"))
            candidate_request = entry["request"]
            candidate_text = _canonical_json(candidate_request)
            ratio = difflib.SequenceMatcher(a=target_text, b=candidate_text).ratio()
            if best is None or ratio > best[0]:
                diff = "\n".join(
                    difflib.unified_diff(
                        candidate_text.splitlines(),
                        target_text.splitlines(),
                        fromfile="nearest_captured",
                        tofile="requested",
                        lineterm="",
                    )
                )
                best = (ratio, candidate_request, diff)
        if best is None:
            return None
        return best[1], best[2]

    async def _verify_canary(self, canary: Canary) -> None:
        committed = self.get(canary.request)
        live_response = await canary.live_fetch()
        if committed is None:
            # First capture: the canary IS the live response, recorded now — the
            # pass that captures it is, by definition, the one that just verified it.
            logger.info("Recording first canary capture", request=canary.request)
            self.put(canary.request, live_response)
            return
        if live_response != committed:
            raise CanaryMismatchError(canary.request, committed, live_response)

    async def fetch(
        self,
        canonical_request: Any,
        live_fetch: Callable[[], Awaitable[Any]],
        *,
        canary: Canary | None = None,
        allow_live: bool = True,
    ) -> Any:
        """The cached response for *canonical_request*, filling and recording a miss live.

        A hit never touches the network — no canary check, no *live_fetch* call.
        A miss with ``allow_live=False`` (replay: nothing may call out) or whose
        *live_fetch*/canary verification raises is a hard failure
        (:class:`CacheMissError` / :class:`CanaryMismatchError`), never a silent
        fallback.
        """
        cached = self.get(canonical_request)
        if cached is not None:
            return cached

        if not allow_live:
            raise CacheMissError(canonical_request, self.nearest(canonical_request), reason="live fetch disabled")

        if canary is not None:
            await self._verify_canary(canary)

        try:
            response = await live_fetch()
        except CanaryMismatchError:
            raise
        except Exception as exc:
            raise CacheMissError(
                canonical_request, self.nearest(canonical_request), reason=str(exc)
            ) from exc

        self.put(canonical_request, response)
        return response

    async def fetch_many(
        self,
        canonical_requests: Sequence[Any],
        live_fill: Callable[[list[Any]], Awaitable[Sequence[Any]]],
        *,
        allow_live: bool = True,
    ) -> list[Any]:
        """:meth:`fetch` over a list of requests, filling every miss in **one** call.

        The responses come back in *canonical_requests*' own order, hits and
        newly recorded misses alike. *live_fill* is handed the missing requests —
        in that same order, duplicates included — and returns one response per
        request it was given; how many network hops it takes to produce them is
        its business, which is the whole point of the method existing. A weather
        window of 31 uncaptured days is one Archive request rather than 31, while
        each day still lands as its own committed entry and is reusable by every
        other window that contains it.

        The policy is :meth:`fetch`'s, unchanged: a miss with ``allow_live=False``
        or a *live_fill* that raises is a hard :class:`CacheMissError` naming the
        first missing request, never a partial answer and never a silent gap.
        Nothing here takes a canary — the caller that batches is the weather one,
        which has no canary because no reproducibility claim rests on its cache
        (``agent_architecture.md`` §5).

        Raises:
            CacheMissError: a miss could not be filled, or *live_fill* answered
                with a different number of responses than it was asked for —
                which would silently mis-align a day with another day's row.
        """
        responses = [self.get(request) for request in canonical_requests]
        missing = [
            request
            for request, response in zip(canonical_requests, responses, strict=True)
            if response is None
        ]
        if not missing:
            return responses

        if not allow_live:
            raise CacheMissError(
                missing[0], self.nearest(missing[0]), reason="live fetch disabled"
            )

        try:
            filled = list(await live_fill(missing))
        except CanaryMismatchError:
            raise
        except Exception as exc:
            raise CacheMissError(
                missing[0], self.nearest(missing[0]), reason=str(exc)
            ) from exc

        if len(filled) != len(missing):
            raise CacheMissError(
                missing[0],
                self.nearest(missing[0]),
                reason=(
                    f"the live fill answered {len(filled)} of {len(missing)} "
                    "requested entries"
                ),
            )

        recorded = dict(zip(map(self.key_for, missing), filled, strict=True))
        for request, response in zip(missing, filled, strict=True):
            self.put(request, response)
        return [
            recorded[self.key_for(request)] if response is None else response
            for request, response in zip(canonical_requests, responses, strict=True)
        ]

"""The surface an answer was computed against, stamped per case.

``expectations.pins`` (``agent_architecture.md`` §6.1) exists because a
materialized answer is only ground truth *relative to something*: the database
that produced it, the GR2L build that simulated it, the station derivation that
aggregated it, and which weather source the window resolved to. A case whose
pins no longer match the run's is not comparable to one that does, and without
the stamp nothing downstream could tell the two apart.

**Stamped where it was read, not everywhere.** An oracle that never called GR2L
carries no canary and an oracle that never touched the station carries no
derivation version — a blanket stamp would assert a dependency the answer does
not have, and would then survive a change that could not possibly have moved it.
This is why every function here is called from an oracle rather than from a
generator loop.

Values are **recomputed** rather than copied out of ``eval/pins.json``, with one
exception: the GR2L canary is a live capture (T115) that no offline process can
reproduce, so it is read from the committed file and its absence is an error
rather than a ``None``. ``just pins`` is what compares the recomputed values with
the committed ones; this module's job is to record what was actually used.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

PINNED_DB = REPO_ROOT / "data" / "water.duckdb"
"""The one database every oracle reads, and the one the rollouts read."""

PINS_FILE = REPO_ROOT / "eval" / "pins.json"

_CHUNK = 1 << 20


class MissingPinError(RuntimeError):
    """A pin an answer depends on has not been captured yet.

    Raised rather than stamped as ``None``: a case emitted with a null canary
    would look pinned and compare against nothing.
    """


@lru_cache(maxsize=8)
def duckdb_sha256(path: Path = PINNED_DB) -> str:
    """sha256 of the pinned database's bytes.

    Cached because it is the same 13 MB file for every case in a split, and
    hashing it per case would dominate generation.
    """
    if not path.exists():
        raise MissingPinError(f"The pinned database is not at {path}.")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def station_derivation_version() -> str:
    """sha256 of the station derivation, canonicalized exactly as ``check_pins`` does.

    The per-field aggregation, the completeness predicate and the day boundary —
    none of which the database hash covers, and all of which move every station
    answer if they move (``weather_station.derivation_pin``).
    """
    from water_assistant_agent.assistant.tools.weather_station import derivation_pin

    return _sha256_json(derivation_pin())


@lru_cache(maxsize=1)
def gr2l_canary() -> str:
    """The committed GR2L request/response canary hash (T115).

    The one pin here that is read rather than computed: it is a hash of a live
    service's response, and an oracle that could recompute it would be calling
    the service twice per case.
    """
    committed = _committed_pins().get("gr2l_canary_response_sha256")
    if not committed:
        raise MissingPinError(
            "gr2l_canary_response_sha256 is unpinned in eval/pins.json; capture it "
            "with `just pins-capture-gr2l` before materializing a modelled answer."
        )
    return committed


def stamp(
    *,
    db_path: Path = PINNED_DB,
    weather_source: Iterable[str] = (),
    used_gr2l: bool = False,
) -> dict[str, Any]:
    """The ``pins`` object for one answer.

    ``duckdb_sha256`` is unconditional — every oracle here reads the pinned
    database, if only for a seed. The rest follow what the answer actually
    touched: *weather_source* is the provenance each window resolved to, and the
    station derivation is stamped exactly when one of them was the station,
    because that is the only case in which the derivation could have moved the
    number.
    """
    sources = sorted(set(weather_source))
    pins: dict[str, Any] = {"duckdb_sha256": duckdb_sha256(db_path)}
    if used_gr2l:
        pins["gr2l_canary"] = gr2l_canary()
    if "station" in sources:
        pins["station_derivation"] = station_derivation_version()
    if sources:
        pins["weather_source"] = sources
    return pins


def _committed_pins() -> dict[str, Any]:
    if not PINS_FILE.exists():
        raise MissingPinError(f"There is no pin file at {PINS_FILE}.")
    return json.loads(PINS_FILE.read_text(encoding="utf-8"))["pins"]


def _sha256_json(value: Any) -> str:
    """sha256 of *value*'s canonical JSON — sorted keys, fixed separators.

    Byte-identical to ``scripts/check_pins.py``'s, because the two hashes are
    compared: a different canonicalization would report drift on every run.
    """
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

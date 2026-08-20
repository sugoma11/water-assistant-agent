"""Verify ``eval/pins.json`` against the tree — the ``just pins`` check.

Architecture §5 lists what a result depends on that no test would otherwise
notice moving: the database, the roof presets, the model service's contract, the
task model's identity and decoding, the dependency versions. Each is recomputed
here from the artifact itself and compared with the committed value, so a run
that quietly changed one of them fails loudly instead of being averaged into a
comparison it no longer belongs to.

Three states per pin. **Pinned** — committed and recomputed alike. **Moved** —
committed and different, which is the failure this script exists for; it exits
non-zero and prints both values. **Unpinned** — committed as ``null`` because
the artifact does not exist yet (the card store, the rules constants, the
reflection model) or because filling it needs a live capture no offline check can
make (the two canary responses). An unpinned entry is reported, never a failure:
this file is written before the artifacts it will pin, and ``--write`` fills each
slot as its packet lands.

Three states, and a **fourth** for the handful of pins no offline run can
recompute at all: a canary response exists only as something a live service
said. Once captured, such a pin is reported as pinned-by-capture and compared
only when a capture is actually run — the alternative, treating "cannot compute"
as "moved", would make ``just pins`` fail permanently the moment the first
canary is committed.

Usage::

    just pins           # verify (exit 1 on a moved pin)
    just pins-write     # re-pin deliberately, then review the diff
    just pins-canary    # capture the GR2L canary live and pin its response hash
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

PINS_PATH = REPO_ROOT / "eval" / "pins.json"
DB_PATH = REPO_ROOT / "data" / "water.duckdb"
CARD_STORE = REPO_ROOT / "src/water_assistant_agent/assistant/knowledge/cards"
RULES_CONSTANTS = REPO_ROOT / "src/water_assistant_agent/assistant/rules_constants.py"
ROOFS = REPO_ROOT / "src/water_assistant_agent/assistant/tools/roofs.py"
LOCKFILE = REPO_ROOT / "uv.lock"

PINNED_DEPENDENCIES = ("google-adk", "litellm", "mlflow", "gepa", "pyyaml", "duckdb")
"""The libraries whose behaviour a result depends on (plan §4)."""

LIVE_ONLY_PINS = frozenset(
    {
        "gr2l_canary_response_sha256",
        "task_model_canary_sha256",
        "reflection_model_canary_sha256",
    }
)
"""Pins whose value only a live service can produce, so this check cannot recompute one.

They are not thereby unverified — the response cache re-fetches the GR2L canary
and compares it byte for byte before recording any new entry, which is a
*stricter* check than this one and runs whenever it matters
(``decisions.md`` § The response cache). What this script can honestly say about
them is which build was captured and when, so a committed value is reported as
pinned rather than failed as uncomputable.
"""

_CHUNK = 1 << 20


def _sha256_file(path: Path) -> str | None:
    """sha256 of *path*'s bytes, or ``None`` when it does not exist."""
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    """sha256 of *value*'s canonical JSON — sorted keys, fixed separators."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_tree(root: Path, pattern: str) -> str | None:
    """One sha256 over every *pattern* file under *root*, path and bytes both.

    Paths are part of the digest because renaming a card is as much a change as
    editing one, and there is no index file to hash instead (§5).
    """
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(root.rglob(pattern)):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _module_version(path: Path) -> str | None:
    """A module's ``VERSION`` constant, read from source without importing it."""
    if not path.exists():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VERSION"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    return None


def _station_derivation() -> dict[str, Any] | None:
    """The station derivation plus the record's first and last complete day.

    The two dates are read **from the database**, through the same
    :class:`StationWeatherSource` the tool uses, rather than transcribed from
    ``findings.md``: a hardcoded span would keep passing after an ingest that
    moved the record's edges, which is the one thing this pin exists to catch
    beyond the derivation itself. Unbounded by any ``as_of`` — this is the
    record's own extent, not a case's view of it.
    """
    if not DB_PATH.exists():
        return None
    from water_assistant_agent.assistant.agents.text_to_sql.executor import (
        DuckDbQueryExecutor,
        create_duckdb_connection,
    )
    from water_assistant_agent.assistant.tools.weather_station import (
        StationWeatherSource,
        derivation_pin,
    )

    executor = DuckDbQueryExecutor(
        connection_factory=lambda: create_duckdb_connection(str(DB_PATH))
    )
    first, last = StationWeatherSource(executor).record_bounds()
    return {
        "derivation_sha256": _sha256_json(derivation_pin()),
        "first_complete_day": None if first is None else first.isoformat(),
        "last_complete_day": None if last is None else last.isoformat(),
    }


def _dependency_versions() -> dict[str, str] | None:
    """The resolved versions of :data:`PINNED_DEPENDENCIES` from ``uv.lock``.

    Read from the lockfile rather than from the installed environment: the
    lockfile is the artifact committed alongside a result, and an environment
    that has drifted from it is exactly what this pin should catch.
    """
    if not LOCKFILE.exists():
        return None
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    resolved = {
        package["name"]: package["version"]
        for package in lock.get("package", [])
        if package.get("name") in PINNED_DEPENDENCIES
    }
    return dict(sorted(resolved.items()))


def compute_pins() -> dict[str, Any]:
    """Recompute every pin this check can derive offline.

    ``None`` means "cannot be computed here" — the artifact does not exist yet,
    or the value needs a live capture — and is what leaves a pin unpinned.
    """
    from water_assistant_agent.assistant.llm import sub_agent_model_pin, task_model_pin
    from water_assistant_agent.assistant.settings import get_settings
    from water_assistant_agent.assistant.tools.gr2l_client import (
        CANARY_REQUEST,
        ROOF_PRESETS,
    )

    settings = get_settings()
    base_url = settings.gr2l_api_base_url
    return {
        "water_duckdb_sha256": _sha256_file(DB_PATH),
        "card_store_sha256": _sha256_tree(CARD_STORE, "*.yaml"),
        "rules_constants_version": _module_version(RULES_CONSTANTS),
        "roofs_version": _module_version(ROOFS),
        "gr2l_roof_presets_sha256": _sha256_json(ROOF_PRESETS),
        # The URL itself stays in `.env`; its hash detects a move to another
        # deployment without publishing an internal host into the repository.
        "gr2l_base_url_sha256": None if base_url is None else _sha256_json(base_url),
        "gr2l_canary_request_sha256": _sha256_json(CANARY_REQUEST),
        "gr2l_canary_response_sha256": None,
        "task_model": task_model_pin(settings),
        "task_model_canary_sha256": None,
        "sub_agent_model": sub_agent_model_pin(settings),
        "reflection_model": None,
        "reflection_model_canary_sha256": None,
        "candidate_prompts": None,
        "station_derivation": _station_derivation(),
        "dependency_versions": _dependency_versions(),
    }


def _load_committed() -> dict[str, Any]:
    if not PINS_PATH.exists():
        raise SystemExit(f"No pins file at {PINS_PATH}; run `just pins-write` to create one.")
    return json.loads(PINS_PATH.read_text(encoding="utf-8"))["pins"]


def _write(pins: dict[str, Any], committed: dict[str, Any]) -> None:
    """Write *pins*, keeping any committed value this run could not recompute.

    A canary response is filled by a live pass, not by this script; ``--write``
    must not wipe one just because it cannot derive it.
    """
    merged = {
        key: (value if value is not None else committed.get(key))
        for key, value in pins.items()
    }
    document = {
        "_comment": (
            "Architecture §5's pin list. Verified by `just pins`; rewritten only by "
            "`just pins-write`. A null is an unpinned slot — the artifact does not "
            "exist yet, or the value needs a live capture."
        ),
        "pins": merged,
    }
    PINS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PINS_PATH.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check(computed: dict[str, Any], committed: dict[str, Any]) -> int:
    """Report every pin; return the process exit code."""
    moved: list[str] = []
    unpinned: list[str] = []

    for key in sorted(set(computed) | set(committed)):
        if key not in committed:
            moved.append(key)
            print(f"MISSING  {key}: not in {PINS_PATH.name}; `just pins-write` to add it")
            continue
        if key not in computed:
            moved.append(key)
            print(f"UNKNOWN  {key}: committed but no longer computed")
            continue
        want, have = committed[key], computed[key]
        if want is None:
            unpinned.append(key)
            state = "unpinned" if have is None else f"unpinned, ready: {_short(have)}"
            print(f"OPEN     {key}: {state}")
        elif have is None and key in LIVE_ONLY_PINS:
            # Captured from a live service and re-verified by the response cache,
            # not here. Committed is the most this run can establish.
            print(f"ok       {key}: {_short(want)} (captured live)")
        elif have is None:
            moved.append(key)
            print(f"MOVED    {key}: committed {_short(want)}, now uncomputable")
        elif want != have:
            moved.append(key)
            print(f"MOVED    {key}\n           committed {_short(want)}\n           found     {_short(have)}")
        else:
            print(f"ok       {key}: {_short(have)}")

    print(
        f"\n{len(computed) - len(moved) - len(unpinned)} pinned, "
        f"{len(unpinned)} unpinned, {len(moved)} moved."
    )
    if moved:
        print("\nA pin moved. Results measured before and after this change are not comparable.")
        return 1
    return 0


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text if len(text) <= 72 else f"{text[:69]}..."


def capture_gr2l_canary() -> int:
    """Send the canary probe to the live GR2L service and pin its response hash.

    The one pin that needs the service standing up. It goes out through
    :func:`~...gr2l_client.fetch_canary` — the same endpoint, headers and body
    every other request uses — so what is pinned here is exactly what a later
    capture pass re-fetches and compares against before recording any cache
    entry. A service that answers differently afterwards is a hard failure by
    design, which is the whole point of committing this (``agent_architecture.md``
    §5, ``decisions.md`` § The response cache).
    """
    import asyncio

    from water_assistant_agent.assistant.tools.gr2l_client import (
        CANARY_REQUEST,
        fetch_canary,
    )

    print(f"Probing GR2L with the canary request: {_sha256_json(CANARY_REQUEST)}")
    try:
        response = asyncio.run(fetch_canary())
    except Exception as exc:  # noqa: BLE001 - the message is the whole output
        print(f"FAILED to reach GR2L: {type(exc).__name__}: {exc}")
        print("Capture cannot start without it; stand the service up and re-run.")
        return 1

    digest = _sha256_json(response)
    committed = _load_committed()
    previous = committed.get("gr2l_canary_response_sha256")
    if previous is not None and previous != digest:
        print(f"MOVED    gr2l_canary_response_sha256\n           committed {previous}")
        print(f"           served    {digest}")
        print("\nThe service has moved under the pin. Results captured before and")
        print("after are not comparable; resolve that before re-pinning.")
        return 1

    committed["gr2l_canary_response_sha256"] = digest
    _write(committed, committed)
    print(f"Pinned   gr2l_canary_response_sha256: {digest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite eval/pins.json from the tree instead of checking it",
    )
    parser.add_argument(
        "--capture-gr2l-canary",
        action="store_true",
        help="probe the live GR2L service and pin its canary response hash",
    )
    args = parser.parse_args()

    if args.capture_gr2l_canary:
        return capture_gr2l_canary()

    computed = compute_pins()
    if args.write:
        committed = json.loads(PINS_PATH.read_text(encoding="utf-8"))["pins"] if PINS_PATH.exists() else {}
        _write(computed, committed)
        print(f"Wrote {PINS_PATH.relative_to(REPO_ROOT)}")
        return 0
    return check(computed, _load_committed())


if __name__ == "__main__":
    raise SystemExit(main())

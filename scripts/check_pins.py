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

    just pins             # verify (exit 1 on a moved pin)
    just pins-write       # re-pin deliberately, then review the diff
    just pins-canary      # capture the GR2L canary live and pin its response hash
    just pins-reflection  # capture the reflection model's canary reply and pin it
    uv run python scripts/check_pins.py --capture-gr2l-canary --accept-moved
                          # ... and accept a canary that has moved, for a service
                          #     that was changed deliberately
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
# `harness/` sits at the repository root and is deliberately outside the
# installed distribution (plan.md §3), so importing `harness.candidates` for the
# candidate-surface pin needs the root on the path as well.
sys.path.insert(0, str(REPO_ROOT))

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
        "candidate_prompt_versions",
    }
)
"""Pins whose value only a live service can produce, so this check cannot recompute one.

They are not thereby unverified — the response cache re-fetches the GR2L canary
and compares it byte for byte before recording any new entry, which is a
*stricter* check than this one and runs whenever it matters
(``decisions.md`` § The response cache). What this script can honestly say about
them is which build was captured and when, so a committed value is reported as
pinned rather than failed as uncomputable.

``candidate_prompt_versions`` joins them for the same reason with a different
service behind it: a version number is what the MLflow registry *assigned*, and
no offline run can derive one. Its companion ``candidate_prompts`` is fully
recomputable, so the pair is verified where it can be — the seed text a version
was registered from is hashed here, and a post-freeze edit to any of the seven
components moves that hash whatever the registry says.
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
    from harness.candidates import candidate_prompts_pin
    from water_assistant_agent.assistant.llm import (
        reflection_model_pin,
        sql_builder_model_pin,
        sql_fixer_model_pin,
        sub_agent_model_pin,
        task_model_pin,
    )
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
        # The text-to-SQL chain is three models, not one: the sub-agent that
        # routes, the builder that writes the query, and the fixer that repairs
        # it. All three are live dependencies under undated aliases and each moves
        # family A's answers on its own, so each is pinned on its own.
        "sub_agent_model": sub_agent_model_pin(settings),
        "sql_builder_model": sql_builder_model_pin(settings),
        "sql_fixer_model": sql_fixer_model_pin(settings),
        # The second model a search depends on and the only one that never
        # appears in a rollout: it writes the candidates the rollouts are scored
        # on, so a swap under it changes what was proposed while leaving every
        # measured surface of the agent untouched (T124).
        "reflection_model": reflection_model_pin(settings),
        "reflection_model_canary_sha256": None,
        # The candidate surface, in two halves. `candidate_prompts` is the
        # registered name and the seed text's hash per optimizable component —
        # seven entries, one per component, never one blob — and it is what makes
        # the T107 freeze checkable: an edit to the handwritten instruction or to
        # a tool docstring moves a hash here. `candidate_prompt_versions` is what
        # the registry assigned and is filled by `just candidates`.
        "candidate_prompts": candidate_prompts_pin(),
        "candidate_prompt_versions": None,
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


def capture_gr2l_canary(*, accept_moved: bool = False) -> int:
    """Send the canary probe to the live GR2L service and pin its response hash.

    The one pin that needs the service standing up. It goes out through
    :func:`~...gr2l_client.fetch_canary` — the same endpoint, headers and body
    every other request uses — so what is pinned here is exactly what a later
    capture pass re-fetches and compares against before recording any cache
    entry. A service that answers differently afterwards is a hard failure by
    design, which is the whole point of committing this (``agent_architecture.md``
    §5, ``decisions.md`` § The response cache).

    *accept_moved* is the deliberate way through that failure, for the case the
    refusal cannot distinguish on its own: the service was **changed on purpose**
    and the new build is the one to measure against. It is a separate flag rather
    than a prompt because re-pinning a moved canary invalidates every result
    captured against the old one, so it should appear in a shell history and in a
    commit message. What it does not do is clean up after itself — cache entries
    recorded from the old build are still the old build's, and the flag prints
    that rather than deleting anything.
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
        if not accept_moved:
            print("\nThe service has moved under the pin. Results captured before and")
            print("after are not comparable; resolve that before re-pinning.")
            print("If the move was deliberate, re-run with --accept-moved.")
            return 1
        print("\n--accept-moved: re-pinning to the served build.")
        print("Everything measured against the committed hash is now incomparable:")
        _report_stale_gr2l_artifacts(previous)

    committed["gr2l_canary_response_sha256"] = digest
    _write(committed, committed)
    print(f"Pinned   gr2l_canary_response_sha256: {digest}")
    return 0


def capture_reflection_canary(*, accept_moved: bool = False) -> int:
    """Probe the live reflection model and pin the hash of its canary reply.

    The optimizer's second model, and the one no rollout ever calls: it writes
    the candidates rather than answering the cases, so nothing else in this
    repository would notice it being swapped. The probe goes out through
    ``harness/reflection.py``'s bound seam — the same endpoint, key and decoding
    parameters a search's reflection call carries — so what is pinned is a fact
    about the request the search issues.

    What is hashed is the assistant message's **content** together with the
    probe, never the response envelope: the envelope's id, timestamp and token
    counts move on every call, and this model's reasoning trace moves with them
    (``findings.md``).

    *accept_moved* is the deliberate way past a moved canary, on
    :func:`capture_gr2l_canary`'s reasoning: a service changed on purpose is
    the one case the refusal cannot tell from a silent swap, and re-pinning
    invalidates every search run made against the old value.
    """
    from harness.reflection import CANARY_PROMPT, probe_canary
    from water_assistant_agent.assistant.settings import get_settings

    settings = get_settings()
    # The reflection model's *own* endpoint, not the shared one. It may be served
    # somewhere else entirely, and printing the shared base here would report a
    # host the probe never reached — about the one field the split exists over.
    endpoint = settings.reflection_extra().get("api_base")
    print(f"Probing the reflection model {settings.reflection_model} at "
          f"{endpoint or 'the provider default'}")
    print(f"  prompt: {CANARY_PROMPT!r}")
    try:
        content, digest = probe_canary(settings)
    except Exception as exc:  # noqa: BLE001 - the message is the whole output
        print(f"FAILED to reach the reflection model: {type(exc).__name__}: {exc}")
        print("A search cannot propose anything without it; fix that and re-run.")
        return 1

    print(f"  reply:  {content.strip()!r}")
    committed = _load_committed()
    previous = committed.get("reflection_model_canary_sha256")
    if previous is not None and previous != digest:
        print(f"MOVED    reflection_model_canary_sha256\n           committed {previous}")
        print(f"           served    {digest}")
        if not accept_moved:
            print("\nThe reflection model has moved under the pin. Candidates proposed")
            print("before and after are not comparable; resolve that before re-pinning.")
            print("If the move was deliberate, re-run with --accept-moved.")
            return 1
        print("\n--accept-moved: re-pinning to the served build.")

    committed["reflection_model_canary_sha256"] = digest
    _write(committed, committed)
    print(f"Pinned   reflection_model_canary_sha256: {digest}")
    return 0


def capture_task_canary(*, accept_moved: bool = False) -> int:
    """Probe the live task model and pin the hash of its canary reply.

    **The last open slot in §5's pin list, and the one a measurement run depends
    on.** The task model is the agent under test and the only optimized one; it
    is served under an undated alias, so a provider-side swap changes every
    measured number while leaving every recorded surface identical. T107 froze
    the testbed with this slot null and the exposure written down: the endpoint
    had moved during that packet and there was nothing in place to catch a swap
    under the move.

    The probe goes out through ``harness/ledger.py``'s witnessed wrapper — ADK's
    own :class:`LiteLlm`, built with the pinned decoding parameters, which is the
    construction a rollout uses — so what is pinned is a fact about the request a
    rollout issues. The served model id comes back with the reply and is printed
    beside it; it is not in the hash, because a provider that merely spells its
    own name differently has not swapped the model.

    Capture this **immediately before a measurement run**. A swap landing between
    the two arms is what the canary exists to detect, and detecting it after the
    fact is the most this mechanism ever offers.

    *accept_moved* is the deliberate way past a moved canary, on
    :func:`capture_gr2l_canary`'s reasoning — and here it is the heaviest of the
    three, because everything measured against the old value is every number the
    thesis reports.
    """
    from harness.ledger import probe_task_canary
    from harness.reflection import CANARY_PROMPT
    from water_assistant_agent.assistant.settings import get_settings

    settings = get_settings()
    print(f"Probing the task model {settings.root_agent_model} at "
          f"{settings.llm_api_base or 'the provider default'}")
    print(f"  prompt: {CANARY_PROMPT!r}")
    try:
        content, served, digest = probe_task_canary(settings)
    except Exception as exc:  # noqa: BLE001 - the message is the whole output
        print(f"FAILED to reach the task model: {type(exc).__name__}: {exc}")
        print("No rollout can run against it, so no measurement can either.")
        return 1

    print(f"  reply:  {content.strip()!r}")
    print(f"  served: {served or '(the endpoint named no model)'}")
    committed = _load_committed()
    previous = committed.get("task_model_canary_sha256")
    if previous is not None and previous != digest:
        print(f"MOVED    task_model_canary_sha256\n           committed {previous}")
        print(f"           served    {digest}")
        if not accept_moved:
            print("\nThe task model has moved under the pin. Every number measured")
            print("before and after is measured on a different agent; resolve that")
            print("before re-pinning. If the move was deliberate, --accept-moved.")
            return 1
        print("\n--accept-moved: re-pinning to the served build.")
        print("Everything measured against the committed hash is now incomparable.")

    committed["task_model_canary_sha256"] = digest
    _write(committed, committed)
    print(f"Pinned   task_model_canary_sha256: {digest}")
    return 0


def _report_stale_gr2l_artifacts(previous: str) -> None:
    """Name what the old canary still stands behind, without touching any of it.

    Deleting would be the wrong reflex twice over: the cache is ``T116``'s to own
    and the committed cases are a record of a measurement that really was taken.
    What re-pinning owes the reader is the list, so the inconsistency is a
    decision someone makes rather than one they discover.
    """
    cache_dir = REPO_ROOT / "eval" / "cache"
    entries = [
        path
        for path in sorted(cache_dir.glob("*.json"))
        if "Ssub" in path.read_text(encoding="utf-8")[:4096]
    ]
    print(f"  - {len(entries)} GR2L response(s) in eval/cache/, recorded from the old build")

    cases_dir = REPO_ROOT / "eval" / "cases"
    for path in sorted(cases_dir.glob("*.json")):
        cases = json.loads(path.read_text(encoding="utf-8"))
        stale = [
            case["inputs"]["case_id"]
            for case in cases
            if case.get("expectations", {}).get("pins", {}).get("gr2l_canary") == previous
        ]
        if stale:
            print(
                f"  - {len(stale)} case(s) in {path.relative_to(REPO_ROOT)} pinned to it: "
                + ", ".join(stale)
            )


MODEL_PIN_KEYS = (
    "task_model",
    "sub_agent_model",
    "sql_builder_model",
    "sql_fixer_model",
    "reflection_model",
)
"""The five pins carrying a ``model_id`` and a ``served_by`` (§5)."""


def verify_provider_whitelist(computed: dict[str, Any]) -> int:
    """A multi-slug provider pin is hard only if each model resolves to ONE server.

    ``served_by`` may name several providers, because the run may span several
    models and no single provider serves them all — T143 puts the root agent on
    ``mistralai/ministral-8b-2512`` (served only by ``mistral``) while the frozen
    text-to-SQL chain and the reflection model stay on deepseek (served here by
    ``gmicloud``). That list is still a hard pin, but **only because the
    intersection of the list with each model's provider set is a singleton**. Let
    a second member into one of those intersections and OpenRouter is free to
    route that model call-by-call again, which is precisely the hazard T134
    introduced the field to close — and nothing in the committed pin would show
    it, because the pin records the list rather than the resolution.

    So the property is verified against the live endpoint listing rather than
    assumed. A single-slug pin needs none of this and is skipped; a multi-slug
    pin that cannot be verified **fails**, because "unverifiable" and "pinned"
    must not print the same way.
    """
    import urllib.error
    import urllib.request

    from water_assistant_agent.assistant.settings import get_settings

    settings = get_settings()
    if not (settings.llm_openrouter_provider or "").strip():
        return 0

    models = sorted(
        {
            computed[k]["model_id"]
            for k in MODEL_PIN_KEYS
            if isinstance(computed.get(k), dict) and computed[k].get("model_id")
        }
    )
    print("\nProvider pin — verifying each model resolves to one server:")
    failed = False
    for model in models:
        # Resolve THIS model, because the setting is a scoped mapping: a bare
        # whitelist cannot pin two models when one provider serves both, which
        # is how glm-5.3 came to be soft-pinned between gmicloud and z-ai (T147).
        slugs = (
            (settings.openrouter_provider_kwargs(settings.llm_api_base, model) or {})
            .get("extra_body", {})
            .get("provider", {})
            .get("only")
            or []
        )
        slug_path = model.split("/", 1)[1] if model.startswith("openrouter/") else model
        url = f"https://openrouter.ai/api/v1/models/{slug_path}/endpoints"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {settings.llm_api_key or ''}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            serving = {
                (endpoint.get("tag") or "").split("/", 1)[0]
                for endpoint in payload["data"]["endpoints"]
            }
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as exc:
            print(f"  UNVERIFIED {model}: {type(exc).__name__}: {exc}")
            failed = True
            continue
        resolved = sorted(serving & set(slugs))
        if len(resolved) == 1:
            print(f"  ok         {model} -> {resolved[0]}")
        elif not resolved:
            print(f"  NO SERVER  {model}: none of {slugs} serves it; every call fails")
            failed = True
        else:
            print(
                f"  SOFT PIN   {model}: {resolved} both serve it, so routing is "
                "free to mix them call by call"
            )
            failed = True
    return 1 if failed else 0


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
    parser.add_argument(
        "--capture-reflection-canary",
        action="store_true",
        help="probe the live reflection model and pin its canary reply hash",
    )
    parser.add_argument(
        "--capture-task-canary",
        action="store_true",
        help="probe the live task model and pin its canary reply hash",
    )
    parser.add_argument(
        "--accept-moved",
        action="store_true",
        help=(
            "with either --capture-*-canary: re-pin even though the canary moved, "
            "for a service that was changed on purpose. Invalidates everything "
            "captured against the old hash; the run prints what."
        ),
    )
    args = parser.parse_args()

    if args.capture_gr2l_canary:
        return capture_gr2l_canary(accept_moved=args.accept_moved)
    if args.capture_reflection_canary:
        return capture_reflection_canary(accept_moved=args.accept_moved)
    if args.capture_task_canary:
        return capture_task_canary(accept_moved=args.accept_moved)

    computed = compute_pins()
    if args.write:
        committed = json.loads(PINS_PATH.read_text(encoding="utf-8"))["pins"] if PINS_PATH.exists() else {}
        _write(computed, committed)
        print(f"Wrote {PINS_PATH.relative_to(REPO_ROOT)}")
        return 0
    status = check(computed, _load_committed())
    # After the pin table, because it is a property OF the pins rather than one
    # of them: `served_by` can be a whitelist, and a whitelist is only a pin
    # while each model in it resolves to one server.
    return max(status, verify_provider_whitelist(computed))


if __name__ == "__main__":
    raise SystemExit(main())

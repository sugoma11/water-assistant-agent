# ── Evaluation testbed (specs/agent_architecture) ─────────────────────────────
# The frozen prompt-optimization testbed: pins, cases, oracles, harness runs.
#
# `pins` checks architecture §5's pin list — the database, the GR2L presets and
# canary, the task model's identity and decoding, the dependency versions. It
# fails when a pin has MOVED, because results measured before and after such a
# change are not comparable. Slots that are not pinned yet (the card store, the
# reflection model, the two canary responses) are reported, never failed; each
# packet fills its own with `pins-write`.

# Verify eval/pins.json against the tree; fails on a moved pin
pins:
    uv run python scripts/check_pins.py

# Re-pin deliberately from the current tree (review the diff before committing)
pins-write:
    uv run python scripts/check_pins.py --write

# Probe the live GR2L service and pin its canary response hash (needs the service up)
pins-canary:
    uv run python scripts/check_pins.py --capture-gr2l-canary

# Probe the live reflection model and pin its canary reply hash (needs the endpoint)
# The optimizer's second model: it writes the candidates rather than answering the
# cases, so nothing else here would notice it being swapped.
pins-reflection:
    uv run python scripts/check_pins.py --capture-reflection-canary

# Probe the live task model and pin its canary reply hash — run this IMMEDIATELY
# before a measurement run. The agent under test is served under an undated alias,
# so a provider-side swap moves every number while leaving every recorded surface
# identical; a swap landing between the two arms is what this detects, and
# detecting it afterwards is the most the mechanism ever offers.
pins-task:
    uv run python scripts/check_pins.py --capture-task-canary

# The candidate surface is seven registered prompts — the root instruction, the five
# tool docstrings and the sub-agent's outward description — and the handwritten
# baseline is registered once as their seed. It is simultaneously the reference arm
# and the search's starting point, so a second copy of that text is what these two
# recipes exist to prevent. Both need a reachable MLflow registry (`just dev-up`).

# Register the handwritten baseline as the seed candidate and pin its versions
candidates:
    uv run python scripts/register_candidates.py

# Verify the pinned versions still hold the handwritten text, byte for byte
candidates-check:
    uv run python scripts/register_candidates.py --check

# Check every `provenance: rendered` card against the constants; prints the correct
# block on drift, so the fix is a paste rather than a transcription. The static cards
# are pinned elsewhere and are not checked here — `just pins` carries them.
cards-check:
    uv run python scripts/check_cards.py

# The cases are generated and never hand-edited: the same seed against the same
# pinned surfaces rewrites the same bytes, so a diff in eval/cases/ is a change in
# the catalog, the record or a pinned surface, and never in the emitter. Both
# recipes need the GR2L service up, because the model families' oracles call it.

# Regenerate eval/cases/{train,test_seen,test_unseen}.json from the catalog
cases:
    uv run python scripts/generate_cases.py

# Byte stability as an exit code: regenerate, diff against what is committed, write nothing
cases-check:
    uv run python scripts/generate_cases.py --check

# The capture pass fills `eval/cache/` by answering every committed case through its
# own oracle live, which is the set of requests the gold answers depend on — a rollout
# would fetch whatever windows the model happened to pick. Needs the GR2L service up,
# and records only against a canary it has just verified.

# Record eval/cache/ over train, test_seen and test_unseen (review the diff, then commit)
capture:
    uv run python scripts/capture_cache.py

# The search reads `train.json` pre-filtered to cases that replay, runs in RECORD mode
# (a miss on a window the candidate chose is recorded, not excluded) and turns the LLM
# response cache ON — the one place it is on, since the measurement run's three repeats
# per condition are the replication. Needs the registry (`just dev-up`), the model
# endpoint and the GR2L service.

# Run one GEPA search over the whole train split at the default budget
search:
    uv run python scripts/run_search.py

# A short search — 3 records, 8 rollouts. Does the whole loop close?
search-smoke:
    uv run python scripts/run_search.py --limit 3 --max-metric-calls 8

# Replay the same cases with the network blocked; fails on any miss or any live call
capture-verify:
    uv run python scripts/capture_cache.py --verify

# The measurement run is the search's opposite on both switches: it replays (a miss
# excludes the case, which is §7's exclusion channel and exists only here) and the LLM
# response cache is OFF, because the three repeats per condition are the replication
# and a prompt-keyed hit would hand the first repeat's bytes to the other two. Run
# `just pins-task` immediately before: a provider swap between the two arms is what
# that canary exists to catch. Needs the registry and the task model's endpoint.

# Two arms × three splits × three repeats, then §7's report
measure:
    uv run python scripts/run_measurement.py

# What was pre-registered, and its sha256 — read this before reading any number
prereg:
    uv run python scripts/run_measurement.py --show-preregistration

# Re-render §7's report from a written measurement; needs no model and no service
measure-report file:
    uv run python scripts/run_measurement.py --analyze {{file}}

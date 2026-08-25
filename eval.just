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

# Replay the same cases with the network blocked; fails on any miss or any live call
capture-verify:
    uv run python scripts/capture_cache.py --verify

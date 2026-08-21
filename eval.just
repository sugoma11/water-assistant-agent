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

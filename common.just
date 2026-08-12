# ── Paths ─────────────────────────────────────────────────────────────────────
# All three trainers split this file 20 train / 55 test (seeded, group-aware), and
# val IS the train split — a copy of the same 20 records, so the val-driven machinery
# (Pareto scoring, keep-best gates) runs on training data and only
# test_quality_{before,after} measures generalization. See
# specs/sampling_refactoring/spec.md. A dataset with <= 20 records therefore yields an
# EMPTY test split: to_test.json (5 records) is a train-path smoke file only.
schema_path    := "src/water_assistant_agent/tenants/green_roof/sensordata.py"
# questions_path := "data/text2sql/deflated_75_sqls_prod.json"
questions_path := "data/text2sql/to_test.json"
db_path        := "data/water.duckdb"
# ── Endpoints ─────────────────────────────────────────────────────────────────
blablador := "blablador"
kisski    := "kisski"
kisski2   := "kisski2"      # KISSKI, second API key (own quota, own base URL var)
kisski3   := "kisski3"      # KISSKI, third API key (own quota, own base URL var)
openrouter := "openrouter"

# from the PRICE_* env vars in .env (per 1M tokens, see .example.env).
train_budget := "3"

# Learning curve: every K EUR of billable spend, each technique pauses at its next
# natural checkpoint and scores its best-so-far prompt on the held-out 55-record test
# split, so quality can be plotted against money (specs/cost-budget-stopping/
# learning-curve-plan.md). "0" turns probing off entirely.
#
# COST: probe spend does NOT charge train_budget -- a probed run buys exactly the same
# optimization as an unprobed one -- but it is real money and real time on top of it.
# One 55-record point costs ~0.35 EUR and ~35 min at the default --probe-workers 1
# (sequential, like the test-before/after phases of the 1.75 EUR triple run these
# numbers come from). At K=0.5 on this 3 EUR budget that is ~5 points: ~1.5 EUR and
# ~3 h added to a ~6 h run. Raise K, or pass --probe-workers > 1, to trade that down.
# Unchanged prompts are served from cache and cost nothing. Shared by all three
# techniques so a comparison's curves are sampled at the same spend levels.
probe_interval_eur := "0.5"

# val_gate_size is the fixed val subset scored for the per-step keep-best
# (0 = gate on the full val set). Val is the 20-record train split, so 12 gates on 60%
# of it and any value >= 20 degenerates to the full set; kept at 12 because gate evals
# are billable against --budget while the full-val baseline is not (spec D5).
textgrad_val_gate_size := "12"

skillopt_edit_budget := "2"
skillopt_minibatch_size := "3"

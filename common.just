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

# val_gate_size is the fixed val subset scored for the per-step keep-best
# (0 = gate on the full val set). Val is the 20-record train split, so 12 gates on 60%
# of it and any value >= 20 degenerates to the full set; kept at 12 because gate evals
# are billable against --budget while the full-val baseline is not (spec D5).
textgrad_val_gate_size := "12"

skillopt_edit_budget := "2"
skillopt_minibatch_size := "3"

# ── Paths ─────────────────────────────────────────────────────────────────────
schema_path    := "src/water_assistant_agent/tenants/green_roof/sensordata.py"
questions_path := "data/text2sql/deflated_75_sqls_prod.json"
db_path        := "data/water.duckdb"
# ── Endpoints ─────────────────────────────────────────────────────────────────
blablador := "blablador"
kisski    := "kisski"
openrouter := "openrouter"

# from the PRICE_* env vars in .env (per 1M tokens, see .example.env).
train_budget := "5"

# val_gate_size is the fixed val subset scored for the per-step keep-best
# (0 = gate on the full val set).
textgrad_val_gate_size := "16"

skillopt_edit_budget := "2"
skillopt_minibatch_size := "3"

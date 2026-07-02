# ── Paths ─────────────────────────────────────────────────────────────────────
schema_path    := "src/water_assistant_agent/tenants/green_roof/sensordata.py"
questions_path := "data/text2sql/deflated_75_sqls_prod.json"
db_path        := "data/water.duckdb"
# ── Endpoints ─────────────────────────────────────────────────────────────────
blablador := "blablador"
kisski    := "kisski"

textgrad_num_epochs := "3"
# GEPA-parity budget: cap optimization task+judge calls (batch forward + per-step gate);
# the two reserved full val passes (baseline + final) sit outside it. val_gate_size is the
# fixed val subset scored for the per-step keep-best (0 = gate on the full val set).
textgrad_metric_call_budget := "100"
textgrad_val_gate_size := "8"

skillopt_num_epochs := "2"
skillopt_edit_budget := "2"
skillopt_minibatch_size := "3"

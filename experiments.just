# command name notation: student-judge-teacher-technique
# http://localhost:5000/#/experiments/1/runs/e4cfbf94efee447f93be9ae21320b89c/evaluations
# too slow
text2sql-train-qwen36-qwen397-qwen397-gepa model=qwen36-35b-blablador endpoint=blablador judge-model=qwen-397b-kisski judge-endpoint=kisski teacher-model=qwen-397b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# http://localhost:5000/#/experiments/1/runs/911263cc351f4cd68ee50b6f36f059b2
# slow too. Have to freeze 3.6 as judge for all the runs
text2sql-train-qwen36-qwen122-qwen397-gepa model=qwen36-35b-blablador endpoint=blablador judge-model=qwen-122b-kisski judge-endpoint=kisski teacher-model=qwen-397b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}


# =========== qwen36 all roles, GEPA, seed 41
text2sql-train-qwen36-qwen36-qwen36-gepa-1 model=qwen36-openrouter-ge endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# qwen36 all roles, GEPA, seed 42
text2sql-train-qwen36-qwen36-qwen36-gepa-2 model=qwen36-openrouter-ge endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# qwen36 all roles, GEPA, seed 43
text2sql-train-qwen36-qwen36-qwen36-gepa-3 model=qwen36-openrouter-ge endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski3 teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="43" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# qwen36 all roles, GEPA, seed 44
text2sql-train-qwen36-qwen36-qwen36-gepa-4 model=qwen36-openrouter-ge endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski2 teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="44" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}


# =========== qwen36 all roles, TextGrad, seed 41
text2sql-train-qwen36-qwen36-qwen36-textgrad-1 model=qwen36-openrouter-tg endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget val-gate-size=textgrad_val_gate_size batch-size=textgrad_batch_size:
    uv run text2sql-train-textgrad --budget {{budget}} --val-gate-size {{val-gate-size}} --batch-size {{batch-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# =========== qwen36 all roles, TextGrad, seed 42
text2sql-train-qwen36-qwen36-qwen36-textgrad-2 model=qwen36-openrouter-tg endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski3 teacher-model=qwen36-35b-kisski teacher-endpoint=kisski3 sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget val-gate-size=textgrad_val_gate_size batch-size=textgrad_batch_size:
    uv run text2sql-train-textgrad --budget {{budget}} --val-gate-size {{val-gate-size}} --batch-size {{batch-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# =========== qwen36 all roles, TextGrad, seed 43
text2sql-train-qwen36-qwen36-qwen36-textgrad-3 model=qwen36-openrouter-tg endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski3 teacher-model=qwen36-35b-kisski teacher-endpoint=kisski3 sampler-seed="43" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget val-gate-size=textgrad_val_gate_size batch-size=textgrad_batch_size:
    uv run text2sql-train-textgrad --budget {{budget}} --val-gate-size {{val-gate-size}} --batch-size {{batch-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# =========== qwen36 all roles, TextGrad, seed 44
text2sql-train-qwen36-qwen36-qwen36-textgrad-4 model=qwen36-openrouter-tg endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="44" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget val-gate-size=textgrad_val_gate_size batch-size=textgrad_batch_size:
    uv run text2sql-train-textgrad --budget {{budget}} --val-gate-size {{val-gate-size}} --batch-size {{batch-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}


# =========== qwen36 all roles, SkillOpt, seed 41
text2sql-train-qwen36-qwen36-qwen36-skillopt-1 model=qwen36-openrouter-so endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --budget {{budget}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}


# =========== qwen36 all roles, SkillOpt, seed 42
text2sql-train-qwen36-qwen36-qwen36-skillopt-2 model=qwen36-openrouter-so endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --budget {{budget}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}

# =========== qwen36 all roles, SkillOpt, seed 43
text2sql-train-qwen36-qwen36-qwen36-skillopt-3 model=qwen36-openrouter-so endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski3 teacher-model=qwen36-35b-kisski teacher-endpoint=kisski3 sampler-seed="43" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --budget {{budget}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}

# =========== qwen36 all roles, SkillOpt, seed 44
text2sql-train-qwen36-qwen36-qwen36-skillopt-4 model=qwen36-openrouter-so endpoint=openrouter judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="44" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --budget {{budget}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}


# ==============================================================================
# Both KISSKI keys: task/student on the second key (`kisski2`), judge + teacher on
# the first (`kisski`). KISSKI's quota is per key, so this keeps the student's
# throughput from competing with the judge/reflection calls. The `-k2` suffix marks
# the two-key wiring (cf. `-or` for openrouter). One recipe per technique, seed
# overridable per run:
#   just text2sql-train-qwen36-qwen36-qwen36-gepa-k2 sampler-seed=42
# ==============================================================================

# =========== qwen36 all roles, GEPA, both KISSKI keys
text2sql-train-qwen36-qwen36-qwen36-gepa-k2 model=qwen36-35b-kisski endpoint=kisski2 judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget:
    uv run text2sql-train-gepa --budget {{budget}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# =========== qwen36 all roles, TextGrad, both KISSKI keys
text2sql-train-qwen36-qwen36-qwen36-textgrad-k2 model=qwen36-35b-kisski endpoint=kisski2 judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget val-gate-size=textgrad_val_gate_size batch-size=textgrad_batch_size:
    uv run text2sql-train-textgrad --budget {{budget}} --val-gate-size {{val-gate-size}} --batch-size {{batch-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# =========== qwen36 all roles, SkillOpt, both KISSKI keys
text2sql-train-qwen36-qwen36-qwen36-skillopt-k2 model=qwen36-35b-kisski endpoint=kisski2 judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path budget=train_budget skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --budget {{budget}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}


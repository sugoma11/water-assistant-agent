# command name notation: student-judge-teacher-technique
# http://localhost:5000/#/experiments/1/runs/e4cfbf94efee447f93be9ae21320b89c/evaluations
# too slow
text2sql-train-qwen36-qwen397-qwen397-gepa model=qwen36-35b-blablador endpoint=blablador judge-model=qwen-397b-kisski judge-endpoint=kisski teacher-model=qwen-397b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# http://localhost:5000/#/experiments/1/runs/911263cc351f4cd68ee50b6f36f059b2
# slow too. Have to freeze 3.6 as judge for all the runs
text2sql-train-qwen36-qwen122-qwen397-gepa model=qwen36-35b-blablador endpoint=blablador judge-model=qwen-122b-kisski judge-endpoint=kisski teacher-model=qwen-397b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# =========== qwen36 all roles, GEPA, seed 41
# http://localhost:5000/#/experiments/1/runs/b2ab55224de84de2b7669ae69aa22e32/evaluations
text2sql-train-qwen36-qwen36-qwen36-gepa-1 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# qwen36 all roles, GEPA, seed 42
# http://localhost:5000/#/experiments/1/runs/51dcc72b3da149f2a9b37778cbe40d8e/evaluations
text2sql-train-qwen36-qwen36-qwen36-gepa-2 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# qwen36 all roles, GEPA, seed 43 
# http://localhost:5000/#/experiments/1/runs/e5920c3e68724a85964381ee117edcd3
text2sql-train-qwen36-qwen36-qwen36-gepa-3 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="43" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# qwen36 all roles, GEPA, seed 44
# http://localhost:5000/#/experiments/1/runs/78ab5951db3d4d6793d0b7c472557026
text2sql-train-qwen36-qwen36-qwen36-gepa-4 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="44" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}


# =========== qwen36 all roles, TextGrad, seed 41
# http://localhost:5000/#/experiments/1/runs/5e32c312ecaf49a284cd963978606cfe
# http://localhost:5000/#/experiments/1/runs/52c922972cbc425fa3e83f9846bac2c8
text2sql-train-qwen36-qwen36-qwen36-textgrad-1 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path epochs=textgrad_num_epochs metric-call-budget=textgrad_metric_call_budget val-gate-size=textgrad_val_gate_size:
    uv run text2sql-train-textgrad --epochs {{epochs}} --metric-call-budget {{metric-call-budget}} --val-gate-size {{val-gate-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

# =========== qwen36 all roles, TextGrad, seed 42
# http://localhost:5000/#/experiments/1/runs/4ebf7f44dc44461e800d58d315b24d4a
text2sql-train-qwen36-qwen36-qwen36-textgrad-2 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path epochs=textgrad_num_epochs metric-call-budget=textgrad_metric_call_budget val-gate-size=textgrad_val_gate_size:
    uv run text2sql-train-textgrad --epochs {{epochs}} --metric-call-budget {{metric-call-budget}} --val-gate-size {{val-gate-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} 

# =========== qwen36 all roles, TextGrad, seed 43
# http://localhost:5000/#/experiments/1/runs/95315217f7d54130aeafc463adfd25f9
text2sql-train-qwen36-qwen36-qwen36-textgrad-3 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="43" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path epochs=textgrad_num_epochs metric-call-budget=textgrad_metric_call_budget val-gate-size=textgrad_val_gate_size:
    uv run text2sql-train-textgrad --epochs {{epochs}} --metric-call-budget {{metric-call-budget}} --val-gate-size {{val-gate-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} 

# =========== qwen36 all roles, TextGrad, seed 44
# 
text2sql-train-qwen36-qwen36-qwen36-textgrad-4 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="44" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path epochs=textgrad_num_epochs metric-call-budget=textgrad_metric_call_budget val-gate-size=textgrad_val_gate_size:
    uv run text2sql-train-textgrad --epochs {{epochs}} --metric-call-budget {{metric-call-budget}} --val-gate-size {{val-gate-size}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} 


# =========== qwen36 all roles, SkillOpt, seed 41
# http://localhost:5000/#/experiments/1/runs/2dec2e47fe9542e6b921f73ebe43c851/evaluations
# http://localhost:5000/#/experiments/1/runs/e6bd65f37905442396da670c44819c4f
# http://localhost:5000/#/experiments/1/runs/0ec7345ce4e043ba9471513ce6d68a0f
text2sql-train-qwen36-qwen36-qwen36-skillopt-1 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path skillopt-num-epochs=skillopt_num_epochs skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --epochs {{skillopt-num-epochs}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}


# =========== qwen36 all roles, SkillOpt, seed 42
# http://localhost:5000/#/experiments/1/evaluation-runs?selectedRunUuid=2695feac03064341a6d7ad8587a3bca7
# http://localhost:5000/#/experiments/1/runs/1c883a55c3fa45c19222b01a49a90dcd
text2sql-train-qwen36-qwen36-qwen36-skillopt-2 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path skillopt-num-epochs=skillopt_num_epochs skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --epochs {{skillopt-num-epochs}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}

# =========== qwen36 all roles, SkillOpt, seed 43
# http://localhost:5000/#/experiments/1/evaluation-runs?selectedRunUuid=c3c969a233a847d3956aa1bb7e7ed064
# http://localhost:5000/#/experiments/1/runs/980ffa0e926947689706908d18116793
text2sql-train-qwen36-qwen36-qwen36-skillopt-3 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="43" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path skillopt-num-epochs=skillopt_num_epochs skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --epochs {{skillopt-num-epochs}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}

# =========== qwen36 all roles, SkillOpt, seed 44
# http://localhost:5000/#/experiments/1/runs/3f091b99738c45f4b2c84f453b2ef9be
# http://localhost:5000/#/experiments/1/runs/7f2c0c2393094edeb82769388939c08e
text2sql-train-qwen36-qwen36-qwen36-skillopt-4 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-kisski teacher-endpoint=kisski sampler-seed="44" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path skillopt-num-epochs=skillopt_num_epochs skillopt-edit-budget=skillopt_edit_budget skillopt-minibatch-size=skillopt_minibatch_size:
    uv run text2sql-train-skillopt --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }} --epochs {{skillopt-num-epochs}} --edit-budget {{skillopt-edit-budget}} --minibatch-size {{skillopt-minibatch-size}}


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
# http://localhost:5000/#/experiments/1/runs/28e3f15bfad04c2c9e7609a62c85ca05/evaluations
text2sql-train-qwen36-qwen36-qwen36-gepa-3 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-blablador judge-endpoint=blablador teacher-model=qwen36-35b-blablador teacher-endpoint=blablador sampler-seed="43" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}


# =========== qwen36 all roles, TextGrad, seed 41
text2sql-train-qwen36-qwen36-qwen36-textgrad-1 model=qwen36-35b-blablador endpoint=blablador judge-model=qwen36-35b-kisski judge-endpoint=kisski teacher-model=qwen36-35b-blablador teacher-endpoint=blablador sampler-seed="41" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path epochs=textgrad_sql_epochs:
    uv run text2sql-train-textgrad --epochs {{epochs}} --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --optimizer-model {{teacher-model}} --optimizer-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

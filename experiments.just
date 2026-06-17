import "models.just"
import "common.just"

# command name notation: student-judge-teacher-technique
text2sql-train-qwen36-qwen397-qwen397-gepa model=qwen36-35b-blablador endpoint=blablador judge-model=qwen-397b-kisski judge-endpoint=kisski teacher-model=qwen35-397b-blablador teacher-endpoint=blablador sampler-seed="42" use-prod-questions="true" questions-path=questions_path schema-path=schema_path db-path=db_path:
    uv run text2sql-train-gepa --questions-path {{questions-path}} --schema-path {{schema-path}} --db-path {{db-path}} --model {{model}} --endpoint {{endpoint}} --judge-model {{judge-model}} --judge-endpoint {{judge-endpoint}} --teacher-model {{teacher-model}} --teacher-endpoint {{teacher-endpoint}} --sampler-seed {{sampler-seed}} {{ if use-prod-questions == "true" { "--use-prod-questions" } else { "" } }}

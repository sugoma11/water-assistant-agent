# Generate domain-specific questions for a water management LLM assistant
# openai/qwen3-235b-a22b looks the best from GAIA
generate-questions model output-dir terms-path num-questions:
    uv run generate-questions --model {{model}} --output-dir {{output-dir}} --terms-path {{terms-path}} --num-questions {{num-questions}}

# Generate questions via a local CLI agent (claude or copilot)
generate-questions-cli tool model output-dir terms-path num-questions:
    uv run generate-questions-cli --tool {{tool}} --model {{model}} --output-dir {{output-dir}} --terms-path {{terms-path}} --num-questions {{num-questions}}

# Generate questions via a local CLI agent using a database schema (synthetic data)
generate-questions-cli-synth tool model output-dir schema-path num-questions:
    uv run generate-questions-cli-synth --tool {{tool}} --model {{model}} --output-dir {{output-dir}} --schema-path {{schema-path}} --num-questions {{num-questions}}

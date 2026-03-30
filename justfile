# Generate domain-specific questions for a water management LLM assistant
generate-questions model output-dir terms-path num-questions:
    uv run generate-questions --model {{model}} --output-dir {{output-dir}} --terms-path {{terms-path}} --num-questions {{num-questions}}

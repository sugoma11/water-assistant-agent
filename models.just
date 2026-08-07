# ── openrouter model aliases ───────────────────────────────────────────────────
# for GEPA: qwen/qwen3.6-35b-a3b (uses litellm beneath) and semicolon for train
# for TextGrad openrouter/qwen/qwen3.6-35b-a3b
# for SkillOpt: openrouter/qwen/qwen3.6-35b-a3b and qwen/qwen3.6-35b-a3b for optimizer
qwen36-openrouter-tg := "openrouter/qwen/qwen3.6-35b-a3b"
qwen36-openrouter-so := "openrouter/qwen/qwen3.6-35b-a3b"
qwen36-openrouter-ge := "openrouter/qwen/qwen3.6-35b-a3b"

# ── Blablador model aliases ───────────────────────────────────────────────────
# value is the API alias id, comment shows the real underlying model
apertus8b              := "openai/alias-apertus"             # swiss-ai/Apertus-8B-Instruct-2509 · 8B dense · MMLU ~63, GSM8K 62.9, HumanEval 44; AA Intelligence 6; 1811 langs
eve-instruct           := "openai/alias-eve"                 # eve-esa/EVE-Instruct · 24B dense (ESA finetune of Mistral-Small-3.2-24B) · Earth-observation/Earth-science domain LLM, RAG-tuned
gpt-oss-120b-blablador := "openai/alias-fast"                # openai/gpt-oss-120b · 117B total / 5.1B active MoE · MMLU-Pro 90
minimax-m27            := "openai/alias-huge"                # MiniMaxAI/MiniMax-M2.7 · 230B total / 10B active MoE · AA Intelligence 50 (v4); 205k ctx
qwen35-122b-blablador  := "openai/alias-large"               # Qwen/Qwen3.5-122B-A10B-FP8 · 122B total / 10B active MoE, multimodal · MMLU-Pro 86.1, GPQA-D 85.5, SWE-bench Verified 72.4; AA 42; 262k ctx
qwen-coder-next        := "openai/alias-code"                # Qwen/Qwen3-Coder-Next-FP8 · 80B total / 3B active MoE · SWE-bench Verified 74.2, AIME25 83; 256k ctx
qwen36-35b-blablador   := "openai/alias-qwen36-35b"          # Qwen/Qwen3.6-35B-A3B-FP8 · 35B total / 3B active MoE, multimodal · SWE-bench Verified 73.4, SWE-bench Pro 49.5, Terminal-Bench 2.0 51.5
minimax-m3             := "openai/alias-minimax-m3"          # MiniMax-M3 (local) · 230B total / 10B active MoE, 1M ctx, multimodal · SWE-bench Pro 59, Terminal-Bench 2.1 66
qwen35-397b-blablador  := "openai/alias-qwen-huge"           # Qwen3.5-397B-A17B (local) · 397B total / 17B active MoE, multimodal · AA Intelligence 45 (#3 open weight); 256k ctx

# Short direct IDs
MiniMax-M3              := "openai/MiniMax-M3"              # local /p/.../MiniMax-M3 · 230B total / 10B active MoE, 1M ctx, multimodal · SWE-bench Pro 59, Terminal-Bench 2.1 66

# Long IDs compressed to valid just variable names
mistral24b          := "openai/mistralai/Mistral-Small-3.1-24B-Instruct-2503" # 24B dense, multimodal · MMLU 80.6, HumanEval 88.4, MATH 69.3; 128k ctx
qwen-397b-blablador := "openai/Qwen3.5-397B-A17B"          # 397B total / 17B active MoE, multimodal · AA Intelligence 45; 256k ctx

# Multifaced Qwen/Qwen3-Embedding-8B
gpt-35-turbo           := "openai/gpt-3.5-turbo"            # Qwen/Qwen3-Embedding-8B
text-davinci-003       := "openai/text-davinci-003"          # Qwen/Qwen3-Embedding-8B
text-embedding-ada-002 := "openai/text-embedding-ada-002"   # Qwen/Qwen3-Embedding-8B
qwen-embed-8b          := "openai/alias-embeddings"          # Qwen/Qwen3-Embedding-8B
qwen3-8b-embeddings    := "openai/alias-qwen3-8b-embeddings" # Qwen/Qwen3-Embedding-8B
faster-whisper-large-v3 := "openai/faster-whisper-large-v3"

mis                    := "openai/alias-mis"                 # models/mis ????????

# ── Kisski model IDs ──────────────────────────────────────────────────────────
apertus-70b        := "openai/apertus-70b-instruct-2509"          # text · swiss-ai Apertus-70B · 70B dense · AA Intelligence 8; 66k ctx, 1811 langs
devstral-123b      := "openai/devstral-2-123b-instruct-2512"      # coding · Mistral Devstral-2-123B · 123B MoE, agentic coding · SWE-bench Verified 72.2, Terminal-Bench 40.5; 256k ctx
deepseek-r1-70b    := "openai/deepseek-r1-distill-llama-70b"      # reasoning · DeepSeek-R1-Distill-Llama-70B · 70B dense (Llama-3.3 base) · AIME24 70, MATH-500 94.5, LiveCodeBench 57.5
gemma4-31b         := "openai/gemma-4-31b-it"                     # text, image · Google Gemma-4-31B · 31B dense, multimodal · GPQA-D 84.3; AA Intelligence 29; 256k ctx
glm47              := "openai/glm-4.7"                            # text · Zhipu GLM-4.7 · 355B total / 32B active MoE · SWE-bench Verified 73.8, AIME25 95.7, LiveCodeBench 84.9; 200k ctx
internvl35-30b     := "openai/internvl3.5-30b-a3b"               # text, image · OpenGVLab InternVL3.5-30B-A3B · 30B total / 3B active MoE, VLM · 83.6 avg over 9 vision-language benchmarks
medgemma-27b       := "openai/medgemma-27b-it"                    # text, image · Google MedGemma-27B (Gemma-3 base) · 27B dense, medical · MedQA 87.7 (text)
llama31-8b         := "openai/meta-llama-3.1-8b-instruct"         # text · Meta-Llama-3.1-8B-Instruct · 8B dense · MMLU 67.4; 128k ctx
mistral-large-675b := "openai/mistral-large-3-675b-instruct-2512" # text, image · Mistral-Large-3-675B-2512 · 675B total / 41B active MoE, multimodal · 256k ctx
qwen3-30b          := "openai/qwen3-30b-a3b-instruct-2507"        # text · Qwen3-30B-A3B-Instruct-2507 · 30.5B total / 3.3B active MoE · 262k ctx
qwen-coder-30b     := "openai/qwen3-coder-30b-a3b-instruct"       # text, code · Qwen3-Coder-30B-A3B · 30.5B total / 3B active MoE · SWE-bench Verified ~51.9; 262k ctx
qwen-omni-30b      := "openai/qwen3-omni-30b-a3b-instruct"        # text, omni · Qwen3-Omni-30B-A3B-Instruct · 30B total / 3B active MoE, any-to-any (text/image/audio/video in); 66k ctx
teuken-7b          := "openai/teuken-7b-instruct-research"        # text · OpenGPT-X Teuken-7B-instruct-research · 7B dense · all 24 EU languages, multilingual EU benchmarks


# Kisski models overlapping with blablador (same underlying model, different API server)
gpt-120b-kisski   := "openai/openai-gpt-oss-120b"                 # text · openai/gpt-oss-120b · 117B total / 5.1B active MoE · MMLU-Pro 90
qwen-122b-kisski  := "openai/qwen3.5-122b-a10b"                   # text, image · Qwen3.5-122B-A10B · 122B/10B MoE · MMLU-Pro 86.1, SWE-bench Verified 72.4; AA 42
qwen-397b-kisski  := "openai/qwen3.5-397b-a17b"                   # text, image · Qwen3.5-397B-A17B · 397B/17B MoE · AA Intelligence 45; 256k ctx
qwen36-35b-kisski := "openai/qwen3.6-35b-a3b"                     # text, image · Qwen3.6-35B-A3B · 35B/3B MoE · SWE-bench Verified 73.4, SWE-bench Pro 49.5


qwen-embed-4b      := "openai/qwen3-embedding-4b"                 # embeddings
e5-mistral-7b      := "openai/e5-mistral-7b-instruct"             # embeddings
e5-multilingual    := "openai/multilingual-e5-large-instruct"     # embeddings

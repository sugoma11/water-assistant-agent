# ── Blablador model aliases ───────────────────────────────────────────────────
# value is the API alias id, comment shows the real underlying model
apertus8b              := "openai/alias-apertus"             # swiss-ai/Apertus-8B-Instruct-2509
eve-instruct           := "openai/alias-eve"                 # eve-esa/EVE-Instruct
gpt-oss-120b-blablador := "openai/alias-fast"                # openai/gpt-oss-120b
minimax-m27            := "openai/alias-huge"                # MiniMaxAI/MiniMax-M2.7
qwen35-122b-blablador  := "openai/alias-large"               # Qwen/Qwen3.5-122B-A10B-FP8
qwen-coder-next        := "openai/alias-code"                # Qwen/Qwen3-Coder-Next-FP8
qwen36-35b-blablador   := "openai/alias-qwen36-35b"          # Qwen/Qwen3.6-35B-A3B-FP8
minimax-m3             := "openai/alias-minimax-m3"          # MiniMax-M3 (local)
mis                    := "openai/alias-mis"                 # models/mis
qwen35-397b-blablador  := "openai/alias-qwen-huge"           # Qwen3.5-397B-A17B (local)

# Short direct IDs
MiniMax-M3              := "openai/MiniMax-M3"              # local /p/.../MiniMax-M3

# Long IDs compressed to valid just variable names
mistral24b          := "openai/mistralai/Mistral-Small-3.1-24B-Instruct-2503"
qwen-397b-blablador := "openai/Qwen3.5-397B-A17B"

# Multifaced Qwen/Qwen3-Embedding-8B
gpt-35-turbo           := "openai/gpt-3.5-turbo"            # Qwen/Qwen3-Embedding-8B
text-davinci-003       := "openai/text-davinci-003"          # Qwen/Qwen3-Embedding-8B
text-embedding-ada-002 := "openai/text-embedding-ada-002"   # Qwen/Qwen3-Embedding-8B
qwen-embed-8b          := "openai/alias-embeddings"          # Qwen/Qwen3-Embedding-8B
qwen3-8b-embeddings    := "openai/alias-qwen3-8b-embeddings" # Qwen/Qwen3-Embedding-8B
faster-whisper-large-v3 := "openai/faster-whisper-large-v3"


# ── Kisski model IDs ──────────────────────────────────────────────────────────
apertus-70b        := "openai/apertus-70b-instruct-2509"          # text
devstral-123b      := "openai/devstral-2-123b-instruct-2512"      # coding
deepseek-r1-70b    := "openai/deepseek-r1-distill-llama-70b"      # reasoning
gemma4-31b         := "openai/gemma-4-31b-it"                     # text, image
glm47              := "openai/glm-4.7"                            # text
internvl35-30b     := "openai/internvl3.5-30b-a3b"               # text, image
medgemma-27b       := "openai/medgemma-27b-it"                    # text, image
llama31-8b         := "openai/meta-llama-3.1-8b-instruct"         # text
mistral-large-675b := "openai/mistral-large-3-675b-instruct-2512" # text, image
qwen3-30b          := "openai/qwen3-30b-a3b-instruct-2507"        # text
qwen-coder-30b     := "openai/qwen3-coder-30b-a3b-instruct"       # text, code
qwen-omni-30b      := "openai/qwen3-omni-30b-a3b-instruct"        # text, omni
teuken-7b          := "openai/teuken-7b-instruct-research"        # text
e5-mistral-7b      := "openai/e5-mistral-7b-instruct"             # embeddings
e5-multilingual    := "openai/multilingual-e5-large-instruct"     # embeddings

# Kisski models overlapping with blablador (same underlying model, different API server)
gpt-120b-kisski   := "openai/openai-gpt-oss-120b"                 # text
qwen-122b-kisski  := "openai/qwen3.5-122b-a10b"                   # text, image
qwen-397b-kisski  := "openai/qwen3.5-397b-a17b"                   # text, image
qwen36-35b-kisski := "openai/qwen3.6-35b-a3b"                     # text, image


qwen-embed-4b      := "openai/qwen3-embedding-4b"                 # embeddings

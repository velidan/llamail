#!/usr/bin/env bash
# Adjust these paths to your llama.cpp build and model locations
LLAMA_SERVER="/path/to/llama-server"
LLM_MODEL="/path/to/Meta-Llama-3.1-8B-Instruct-Q8_0.gguf"
EMBED_MODEL="/path/to/nomic-embed-text-v2-moe.Q6_K.gguf"

# LLM Server (Llama 3.1 8B)
$LLAMA_SERVER \
  -m "$LLM_MODEL" \
  --port 11434 \
  --ctx-size 8192 \
  --n-gpu-layers 99 \
  --flash-attn on &

# Embedding Server (Nomic v2 MoE)
$LLAMA_SERVER \
  -m "$EMBED_MODEL" \
  --port 11435 \
  --embeddings \
  --ctx-size 512 \
  --n-gpu-layers 99 &

wait

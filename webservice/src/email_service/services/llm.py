import json
import logging
import httpx
from email_service.config import settings

logger = logging.getLogger(__name__)

# Note: We use httpx directly because llama.cpp follows the OpenAI API standard
# (/v1/chat/completions, /v1/embeddings).


def generate(prompt: str, system: str = "", json_mode: bool = True) -> str:
    """Generates text using the llama.cpp chat/completions endpoint."""
    if not prompt or not prompt.strip():
        return json.dumps({"summary": "No content to process", "category": "none"})

    # llama.cpp OpenAI-compatible endpoint
    url = f"{settings.llm_url}/v1/chat/completions"

    payload = {
        "model": settings.llm_model,
        "messages": [
            {
                "role": "system",
                "content": system
                or (
                    "You are a helpful assistant that outputs only JSON."
                    if json_mode
                    else "You are a helpful assistant."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        with httpx.Client(timeout=90.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            # Standard OpenAI/llama.cpp response structure
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"LLM Generation failed: {e}")
        return json.dumps({"summary": f"Error during generation: {str(e)}"})


def embed(text: str, is_query: bool = False) -> list[float]:
    """Generates embeddings using the llama.cpp embedding endpoint."""
    # llama.cpp standard path is /v1/embeddings
    url = f"{settings.embed_url}/v1/embeddings"

    payload = {"input": text, "model": settings.embedding_model}

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            # Standard OpenAI embedding structure: data['data'][0]['embedding']
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.error(f"Embedding failed at {url}: {e}")
        # Re-raise so the worker knows the task failed and should be retried
        raise


def is_available() -> bool:
    """Checks if the llama.cpp servers are responding."""
    try:
        # llama.cpp health check endpoint
        with httpx.Client(timeout=2.0) as client:
            llm_ok = client.get(f"{settings.llm_url}/health").status_code == 200
            embed_ok = client.get(f"{settings.embed_url}/health").status_code == 200
            return llm_ok and embed_ok
    except Exception:
        return False

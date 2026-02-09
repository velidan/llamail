import ollama

from email_service.config import settings

client = ollama.Client(host=settings.ollama_url)


def generate(prompt: str, system: str = "") -> str:
    response = client.chat(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return response["message"]["content"]


def embed(text: str) -> list[float]:
    response = client.embed(model=settings.embedding_model, input=text)
    return response["embeddings"][0]


def is_available() -> bool:
    try:
        client.list()
        return True
    except Exception:
        return False

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EMAIL_")

    ollama_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1:8b"
    embedding_model: str = "nomic-embed-text"

    db_path: Path = Path("data/emails.db")

    chroma_path: Path = Path("data/chroma")

    max_context_tokens: int = 8192
    summary_content_budget: int = 4000
    summary_response_budget: int = 1500
    chunk_threshold: int = 3500
    chunk_size: int = 3000
    chunk_overlap: int = 500

    host: str = "0.0.0.0"
    port: int = 8080


settings = Settings()

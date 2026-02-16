from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EMAIL_")

    llm_url: str = "http://localhost:11434"
    embed_url: str = "http://localhost:11435"
    
    # These names can stay the same or be generic
    llm_model: str = "llama3.1"
    embedding_model: str = "nomic-v2"

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

    gmail_credentials_path: Path = Path("credentials/client_secret.json")
    gmail_token_path: Path = Path("credentials/token.json")
    gmail_scopes: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]
    import_batch_size: int = 50
    import_max_retries: int = 3


settings = Settings()

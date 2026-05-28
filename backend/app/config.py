from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FREE_WEBUI_", env_file=".env", extra="ignore")

    # OpenAI-compatible upstream. Defaults target a local Ollama OpenAI-compat server.
    upstream_base_url: str = "http://localhost:11434/v1"
    upstream_api_key: str = "ollama"
    default_model: str = "llama3.2"

    # Ollama native admin URL (used for pulling/deleting models). If empty,
    # derived from upstream_base_url by stripping the trailing /v1.
    ollama_admin_url: str = ""

    # SQLite file path. Created on first run.
    db_path: str = "data/free-webui.db"

    # Session cookie signing key. If empty, a persistent random key is
    # generated and stored next to the DB.
    secret_key: str = ""
    secret_key_path: str = "data/secret.key"

    # Session cookie max age (seconds). Default: 30 days.
    session_max_age: int = 60 * 60 * 24 * 30

    # RAG: embedding model + chunking params (set embedding_model to
    # something your upstream actually serves, e.g. nomic-embed-text).
    embedding_model: str = "nomic-embed-text"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    rag_top_k: int = 5
    rag_max_upload_bytes: int = 20 * 1024 * 1024  # 20 MB

    # CORS: SvelteKit dev server.
    allowed_origins: list[str] = ["http://localhost:5173"]


settings = Settings()

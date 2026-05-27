from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FREE_WEBUI_", env_file=".env", extra="ignore")

    # OpenAI-compatible upstream. Defaults target a local Ollama OpenAI-compat server.
    upstream_base_url: str = "http://localhost:11434/v1"
    upstream_api_key: str = "ollama"
    default_model: str = "llama3.2"

    # CORS: SvelteKit dev server.
    allowed_origins: list[str] = ["http://localhost:5173"]


settings = Settings()

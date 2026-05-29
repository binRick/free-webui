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

    # Web search: SearXNG-compatible endpoint (returns JSON when ?format=json).
    # Leave empty to disable globally.
    searxng_url: str = ""
    web_search_top_k: int = 5
    web_search_timeout_seconds: float = 10.0

    # Image generation. Set image_backend to one of "openai", "automatic1111",
    # or "comfyui" to expose the built-in `imagine` tool. Leave empty to disable.
    image_backend: str = ""
    # Base URL of the image backend, e.g. https://api.openai.com/v1 (openai),
    # http://localhost:7860 (automatic1111), http://localhost:8188 (comfyui).
    image_base_url: str = ""
    image_api_key: str = ""  # bearer token for the openai-style backend
    image_model: str = "dall-e-3"  # openai model id / sd checkpoint name
    image_size: str = "1024x1024"  # default WxH; clients may override per call
    image_steps: int = 25  # sampling steps (automatic1111 / comfyui)
    image_timeout_seconds: float = 180.0
    image_max_dimension: int = 2048  # clamp requested width/height to this
    image_max_bytes: int = 10 * 1024 * 1024  # reject generated images larger than this
    # ComfyUI only: path to an API-format workflow JSON template. The tokens
    # %prompt%, %negative_prompt%, %width%, %height%, %seed%, %steps% are
    # substituted. If empty, a built-in SD1.5 txt2img graph is used.
    comfyui_workflow_path: str = ""

    # Code interpreter. Exposes the built-in `run_python` tool. Backends:
    #   "docker"     — strongest isolation (no network, read-only rootfs,
    #                  non-root, dropped caps, mem/cpu/pids limits). Preferred.
    #   "subprocess" — same-host subprocess: timeouts + RLIMITs + stripped env,
    #                  but NOT a security boundary (the code can read the host
    #                  filesystem). Only for trusted, single-user deployments.
    #   "auto"       — docker if the docker binary is present, else subprocess.
    #   ""           — disabled (default); the tool is not offered.
    code_interpreter: str = ""
    code_docker_image: str = "python:3-alpine"
    code_timeout_seconds: float = 15.0
    code_max_memory_mb: int = 512
    code_cpus: str = "1.0"  # docker --cpus
    code_pids_limit: int = 128  # docker --pids-limit / subprocess RLIMIT_NPROC (linux)
    code_max_output_chars: int = 20000  # cap captured stdout/stderr beyond this
    code_max_concurrency: int = 2  # max simultaneous executions across all chats

    # Pipelines / plugin framework. Point at a directory of operator-installed
    # Python plugin modules (inlet/outlet hooks) to enable it; empty = disabled.
    # Plugins are TRUSTED in-process code — do not load from untrusted sources.
    plugins_dir: str = ""
    # Per-hook wall-clock cap. NOTE: it only fires at await points — a
    # synchronously blocking hook cannot be interrupted and will stall the whole
    # server, so plugins must do blocking I/O / heavy CPU work in a thread.
    plugins_timeout_seconds: float = 5.0

    # CORS: SvelteKit dev server.
    allowed_origins: list[str] = ["http://localhost:5173"]


settings = Settings()

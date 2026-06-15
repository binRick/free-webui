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

    # Set the session cookie's Secure flag (only sent over HTTPS). Leave False
    # for plain-HTTP localhost; set True when served over HTTPS in production.
    cookie_secure: bool = False

    # Login throttling: max attempts per (client IP, username) within the
    # rolling window before /login returns 429. Set login_rate_limit=0 to disable.
    login_rate_limit: int = 10
    login_rate_window_seconds: float = 60.0

    # Outbound SSRF guard for user-/operator-supplied URLs (MCP server URLs,
    # image-backend result URLs). When on, refuses link-local / multicast /
    # reserved / unspecified ranges always, and loopback + private (RFC1918/ULA)
    # ranges when ssrf_block_private is set. Add trusted hosts or CIDRs to
    # ssrf_allow_hosts (e.g. ["127.0.0.1", "localhost", "10.0.0.0/8"]) to permit
    # a local/LAN MCP server. Unresolvable hosts are allowed (they can't be
    # connected to, so are not an SSRF target).
    ssrf_protection: bool = True
    ssrf_block_private: bool = True
    ssrf_allow_hosts: list[str] = []

    # Reject request bodies larger than this many bytes (coarse DoS backstop;
    # sits above rag_max_upload_bytes so document uploads still work). 0 disables.
    max_request_body_bytes: int = 32 * 1024 * 1024

    # Finite read timeout (seconds) for the upstream LLM connection — the max
    # gap between streamed tokens. Prevents a stalled upstream from hanging a
    # stream forever (was previously unbounded).
    upstream_read_timeout_seconds: float = 300.0

    # Emit Strict-Transport-Security. Only meaningful behind HTTPS; off by
    # default so a plain-HTTP localhost can't poison a browser's HSTS cache.
    security_hsts: bool = False

    # Allow users to create public read-only share links for conversations.
    allow_public_sharing: bool = True

    # After the first exchange, ask the model for a short conversation title
    # (POST /api/conversations/{id}/autotitle). Falls back to the first-message
    # heuristic on any failure. Set false to keep the heuristic title.
    auto_title: bool = True

    # After a reply, offer a few suggested follow-up questions (a lightweight
    # upstream call, like auto_title). Set false to disable.
    suggest_followups: bool = True

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

    # Server-side voice: OpenAI-compatible proxies for speech-to-text
    # (Whisper `/audio/transcriptions`) and text-to-speech (`/audio/speech`).
    # Point the base URLs at any compatible server (OpenAI, faster-whisper-server,
    # speaches, openai-edge-tts, kokoro, …). Leave a base URL empty to disable
    # that direction — the client then falls back to the browser Web Speech API.
    audio_stt_base_url: str = ""
    audio_stt_api_key: str = ""
    audio_stt_model: str = "whisper-1"
    audio_tts_base_url: str = ""
    audio_tts_api_key: str = ""
    audio_tts_model: str = "tts-1"
    audio_tts_voice: str = "alloy"
    audio_tts_format: str = "mp3"  # response_format for /audio/speech
    audio_timeout_seconds: float = 120.0
    audio_max_upload_bytes: int = 25 * 1024 * 1024  # reject audio uploads over this

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

    # OIDC / OAuth2 single sign-on. Set issuer + client_id + client_secret to
    # expose a "Sign in with <provider>" button. Uses the authorization-code
    # flow with server-side token exchange + the userinfo endpoint (no JWT
    # crypto / extra deps). redirect_uri must point at this backend's
    # /api/auth/oidc/callback (works cleanly in a same-origin production deploy).
    oidc_issuer: str = ""  # e.g. https://accounts.google.com
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""  # e.g. https://chat.example.com/api/auth/oidc/callback
    oidc_scopes: str = "openid email profile"
    oidc_provider_name: str = "SSO"  # button label
    oidc_allow_signup: bool = True  # auto-provision users who sign in for the first time
    oidc_admin_emails: list[str] = []  # emails granted the admin role on provision
    oidc_post_login_redirect: str = "/"  # where to send the browser after login
    # Allow http(s)-insecure OIDC endpoints. Default off: the issuer + all
    # discovered endpoints must be https so the client_secret / tokens can't
    # travel in cleartext. Enable only for local testing.
    oidc_insecure_transport: bool = False

    # CORS: SvelteKit dev server.
    allowed_origins: list[str] = ["http://localhost:5173"]


settings = Settings()


def oidc_enabled() -> bool:
    return bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_client_secret)

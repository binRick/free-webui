# Integration harness

Spins up a **real open-source model** (via [Ollama](https://ollama.com)) behind
the **actual free-webui backend** with Docker Compose, then runs an HTTP test
suite against the live stack. This is the end-to-end counterpart to the unit
tests in `backend/tests/`, which mock the upstream LLM — here nothing is mocked.

## Requirements

- Docker + Docker Compose v2 (`docker compose version`)
- ~2 GB disk + bandwidth for the first model pull (cached afterwards in a named
  volume, so later runs are fast)
- No GPU needed — the defaults are small, CPU-friendly models.

## Run it

```sh
cd integration
./run.sh                 # build, pull the model, run the suite, tear down
```

The script exits with the pytest exit code, so it doubles as a local gate.

Flags:

| Flag         | Effect                                                        |
| ------------ | ------------------------------------------------------------- |
| `--keep`     | leave the stack running afterwards (inspect / re-run faster)  |
| `--no-build` | skip the image build and reuse the existing one               |

## What it covers

| Test                                  | Asserts (hard)                                              | Soft (xfail on miss) |
| ------------------------------------- | ----------------------------------------------------------- | -------------------- |
| `test_health`                         | `/api/health` is up                                         | —                    |
| `test_models_lists_upstream_model`    | `/api/models` lists the pulled model                        | —                    |
| `test_chat_round_trip_*`              | a turn streams non-empty content and persists user+assistant| —                    |
| `test_multiturn_keeps_history`        | both turns persist (4 messages)                             | model recalls a fact |
| `test_rag_upload_embeds_and_can_ground` | doc upload + **real `/v1/embeddings`** succeed             | model uses the fact  |
| `test_tool_loop_calculator`           | **if** the model calls `calculate`, the loop result is `391`| model calls the tool |
| `test_plugin_outlet_marks_persisted_text` | the mounted outlet plugin's marker is on the persisted (not streamed) message — **fully deterministic** | — |

Because real models are non-deterministic, "is the model smart enough" checks
(RAG recall, tool choice) `xfail` instead of failing — a tiny CPU model still
gives a green, meaningful run. The plugin test is model-independent and always
asserts hard.

## Choosing models

Defaults: `qwen2.5:1.5b` (chat, tool-capable) + `nomic-embed-text` (embeddings).
Override per run via `integration/.env` (copy `.env.example`) or inline:

```sh
MODEL=llama3.2:3b EMBED_MODEL=nomic-embed-text ./run.sh   # more reliable tools
MODEL=qwen2.5:0.5b ./run.sh                               # fastest smoke run
```

## Poke at the stack manually

```sh
cd integration
# expose ports first: uncomment the `ports:` lines for `backend` (and `ollama`)
docker compose up -d backend            # ollama -> model pull -> backend, healthy
curl localhost:8000/api/health
docker compose run --rm tests           # run the suite against the running stack
docker compose down -v                  # stop + drop the model cache volume
```

You can also run the suite from the host (no test container) once `backend`'s
port is published:

```sh
BASE_URL=http://localhost:8000 MODEL=qwen2.5:1.5b \
  PYTHONPATH= pytest -v integration/tests
```

## Notes

- `backend` waits for `ollama-init` (the model puller) to finish, so the suite
  always has its models.
- The backend runs with the plugin framework enabled (`FREE_WEBUI_PLUGINS_DIR`
  → `integration/plugins/`), which is how the deterministic plugin test works.
- A repo-root `.dockerignore` keeps the build context small (the backend image
  only needs `backend/`).
- This stack is for **testing only** — it ships a hard-coded secret key and an
  open model server on the compose network. Don't expose it.

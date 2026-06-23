# Feature → test traceability matrix

Every feature free-webui **claims** (see the README parity table) maps here to
the test(s) that **prove** it. The rule: *if we claim it, a test covers it.*
This is the spine of "methodically test every feature" — a claim with no linked
test is a bug in this table.

## Test layers

| Layer | Where | What it proves | Runs in CI |
| --- | --- | --- | --- |
| **Backend** (pytest) | `backend/tests/` — 491 tests | API/engine behaviour, persistence, permissions, wire shapes | ✅ on **SQLite + Postgres** |
| **E2E** (Playwright) | `frontend/e2e/` — 7 specs | the real UI↔backend↔upstream path under a headless browser | ✅ (`e2e` job) |
| **Contract** | `test_openai_compat`, `test_anthropic_compat`, `test_conversations_streaming` | the `/v1`, Anthropic, and SSE-frame shapes clients depend on | ✅ |
| **Live integration** | `integration/` (docker-compose + real Ollama) | end-to-end against a real model | manual / opt-in |
| **Manual smoke** | `docs/TESTPLAN.md` | real-model quality, voice, image backends | release checklist |

E2E runs against a **deterministic stdlib mock upstream** (`frontend/e2e/mock_upstream.py`)
so specs are reproducible; it echoes the last user message and honours triggers
`[[slow]]` / `[[artifact]]` / `[[tool]]`.

## Matrix

Status: ✅ covered · ⚠️ partial / one-layer · ⬜ manual-only.

### Chat core
| Feature | Backend tests | E2E | Status |
| --- | --- | --- | --- |
| Streaming chat + persistence | `test_conversations`, `test_conversations_streaming` | `smoke.spec` | ✅ |
| Edit user msg → truncate + rerun | `test_message_actions`, `test_conversations_phase2` | `chat.spec` | ✅ |
| **Edit assistant msg in place** | `test_message_actions` | `chat.spec` | ✅ |
| Regenerate any turn + variants | `test_message_actions`, `test_conversations_phase2` | `chat.spec` | ✅ |
| Continue generation | `test_message_actions` | — | ⚠️ backend |
| Delete from here (truncate) | `test_message_actions` | — | ⚠️ backend |
| **Queue while streaming** | — (frontend-only) | `chat.spec` | ✅ E2E |
| **Persisted tool calls** (reload) | `test_conversations_streaming` | `tools.spec` | ✅ |
| Collapsible reasoning `<think>` | `test_reasoning` | — | ⚠️ backend |
| Client-disconnect abort | `test_disconnect_abort` | — | ⚠️ backend |
| Context/token budgeting | `test_context_budget` | — | ⚠️ backend |
| Clone conversation | `test_clone` | — | ⚠️ backend |
| Export / import | `test_export`, `test_import` | — | ⚠️ backend |
| Temporary chat | `test_temporary_chat` | — | ⚠️ backend |

### Knowledge / RAG
| Feature | Backend tests | E2E | Status |
| --- | --- | --- | --- |
| Doc upload + retrieval | `test_rag`, `test_citations` | `knowledge.spec` | ✅ |
| **Office extraction** (.docx/.xlsx/.pptx) + external | `test_extract_office` | — | ⚠️ backend |
| **Full-context mode** | `test_full_context` | `knowledge.spec` | ✅ |
| Hybrid (dense+BM25) + rerank | `test_rag` | — | ⚠️ backend |
| Collections / knowledge bases | `test_collections` | — | ⚠️ backend |
| URL ingest | `test_web_loader` | — | ⚠️ backend |
| Web search + citations | `test_web_search`, `test_citations` | — | ⚠️ backend |
| Citation redaction on share | `test_citations`, `test_shares` | — | ⚠️ backend |

### Tools
| Feature | Backend tests | E2E | Status |
| --- | --- | --- | --- |
| Function-calling loop | `test_tools`, `test_conversations_streaming` | `tools.spec` | ✅ |
| MCP servers | `test_mcp` | — | ⚠️ backend |
| OpenAPI tool servers | `test_openapi_tools` | — | ⚠️ backend |
| Code interpreter (sandbox) | `test_code_exec` | — | ⚠️ backend |
| Image generation | `test_images` | — | ⚠️ backend |
| **Artifacts** (sandboxed iframe) | — (frontend-only) | `artifacts.spec` | ✅ E2E |
| Plugins / pipelines | `test_plugins` | — | ⚠️ backend |

### Multi-user / admin / auth
| Feature | Backend tests | E2E | Status |
| --- | --- | --- | --- |
| argon2 auth + sessions/revocation | `test_auth` | `smoke.spec` (login via global-setup) | ✅ |
| OIDC / SSO | `test_oidc` | — | ⚠️ backend |
| Groups + per-model access | `test_access` | — | ⚠️ backend |
| Per-feature permission matrix | `test_permissions` | — | ⚠️ backend |
| Admin user mgmt | `test_admin_users` | `admin.spec` | ✅ |
| **Account suspension** (enable/disable) | `test_admin_users` | `admin.spec` | ✅ |
| **Self-service password change** | `test_account` | — | ⚠️ backend |
| Audit log | `test_audit` | — | ⚠️ backend |
| Feedback log | `test_admin_feedback` | — | ⚠️ backend |
| Usage analytics | `test_admin_analytics` | `admin.spec` | ✅ |
| **Token + cost analytics** | `test_admin_analytics` | `admin.spec` | ✅ |
| Broadcast banners | `test_banners` | — | ⚠️ backend |
| Signup webhooks | `test_webhooks` | — | ⚠️ backend |
| Data export + account deletion | `test_account`, `test_export` | — | ⚠️ backend |
| API keys (`/v1` bearer) | `test_api_keys` | — | ⚠️ backend |

### Models / connections / eval
| Feature | Backend tests | E2E | Status |
| --- | --- | --- | --- |
| Multiple upstream connections | `test_connections` | — | ⚠️ backend |
| Ollama model management | `test_admin_models` | — | ⚠️ backend |
| Presets / custom assistants | `test_presets` | — | ⚠️ backend |
| Arena + leaderboard | `test_evaluations` | — | ⚠️ backend |

### Organization / collab
| Feature | Backend tests | E2E | Status |
| --- | --- | --- | --- |
| **First-class tags** + rename/merge | `test_tags` | `tags.spec` (autocomplete) | ✅ |
| Folders | `test_folders` | — | ⚠️ backend |
| Pin / archive / search | `test_conversations_phase2` | — | ⚠️ backend |
| Notes | `test_notes` | — | ⚠️ backend |
| Memories | `test_memories` | — | ⚠️ backend |
| Prompt library | `test_prompts` | — | ⚠️ backend |
| Public share links | `test_shares` | — | ⚠️ backend |
| Real-time channels | `test_channels`, `test_channels_ws`, `test_channels_redis` | — | ⚠️ backend |

### API / platform
| Feature | Backend tests | E2E | Status |
| --- | --- | --- | --- |
| OpenAI-compatible `/v1` (contract) | `test_openai_compat` | — | ✅ contract |
| Anthropic `/v1/messages` proxy | `test_anthropic_compat` | — | ✅ contract |
| Voice STT/TTS proxy | `test_audio` | — | ⚠️ backend |
| Object store (S3) | `test_objectstore`, `test_files` | — | ⚠️ backend |
| Postgres backend | whole suite via `test_database`/`test_pg_pool` | — | ✅ (CI matrix) |
| SSRF guard / security headers | `test_netguard`, `test_security` | — | ✅ |
| Appearance / branding | `test_appearance` | — | ⚠️ backend |

## Known coverage gaps (next E2E targets)

UI-facing features still proven only at the backend layer — the highest-value
specs to add next:

- Continue generation, delete-from-here (UI buttons)
- Collapsible reasoning render + collapse
- Collections attach in the chat drawer
- Compare-models / arena vote UI
- Voice/call mode UI, image-gen render
- Self-service password change UI (backend solid; needs a throwaway-user spec
  so it doesn't invalidate the shared admin session)

## Running each layer

```sh
# backend (SQLite)
cd backend && python -m pytest -q
# backend (Postgres) — needs a postgres + FREE_WEBUI_TEST_DATABASE_URL
# E2E (boots mock upstream + backend + vite automatically)
cd frontend && npm run e2e          # npm run e2e:ui to watch
# live integration (real Ollama)
cd integration && ./run.sh
```

## Parity context

Scored at **~63% overall** (71% on core+common) vs Open WebUI **v0.9.6** — see
the parity scorecard. free-webui is strongest on the daily-driver chat surface
(chat-UX ~92%) and lighter on enterprise breadth (vector-DB backends, the
Tools/Pipe/Filter function framework, LDAP/SCIM). The matrix above tracks test
coverage of what we *do* ship.

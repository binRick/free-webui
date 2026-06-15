# Changelog

This project uses [Keep a Changelog](https://keepachangelog.com/) conventions
and semantic versioning. Dates are not pinned to releases yet (pre-1.0).

## [Unreleased]

The 0.1 line grew from "core chat only" into a full self-host chat platform.
Highlights since the initial tiers (see git history + `docs/ROADMAP.md` for the
authoritative status):

### Added
- **Security hardening**: SSRF guard for user/operator URLs, authenticated
  `/api/models`, security-headers middleware, login rate-limiting, request
  body cap, finite upstream read timeout, bounded `calculate()`,
  `cookie_secure`/HSTS flags, request-id middleware.
- **Auth/RBAC**: server-side session revocation (`token_version`,
  logout-everywhere), **OIDC/SSO** (no extra dep), user **groups + per-model
  access control**, admin **audit log** and **feedback log**.
- **Chat UX**: sidebar search + date grouping + rename + **pin/archive**,
  **non-destructive regenerate with variant navigation**, copy + 👍/👎 per
  message, **LLM auto-titling**, extra generation params
  (`max_tokens`/penalties/seed).
- **Knowledge bases**: reusable document **collections** attachable to any chat.
- **Connectivity**: **multiple upstream connections** with per-model routing;
  hardened OpenAI `/v1` surface (+`/v1/embeddings`); **Anthropic `/v1/messages`
  proxy**.
- **Sharing**: public read-only conversation **share links**.

### Engineering
- Programmable upstream test fixture; **220+ backend tests**; ruff lint;
  GitHub Actions CI (lint + pytest matrix + frontend check).
- Security-sensitive features shipped with adversarial multi-agent review.
- Governance: `SECURITY.md`, `CONTRIBUTING.md` (clean-room attestation), PR
  template; planning docs under `docs/`.

### Fixed
- Migration ordering (indexes created after column migrations); the always-200
  test stub that hid every core error path; account-takeover via unverified
  OIDC email; fail-open model-access grants; per-request `/models` fan-out.

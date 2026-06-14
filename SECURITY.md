# Security policy

free-webui is self-hosted software that proxies untrusted model output, runs
operator-configured tools, and (optionally) executes code. We take security
reports seriously.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately via GitHub's **Report a vulnerability** button
(Security → Advisories → *Report a vulnerability*) on this repository, or email
the maintainers. Include:

- a description of the issue and its impact,
- steps to reproduce (a PoC if possible),
- affected version / commit,
- any suggested remediation.

We aim to acknowledge a report within a few days and to ship a fix or mitigation
as quickly as severity warrants. Please give us a reasonable window to remediate
before any public disclosure.

## Scope & deployment notes

Some behaviours are **intentional trade-offs**, configurable by the operator —
please read before reporting:

- **Plugins (`FREE_WEBUI_PLUGINS_DIR`)** are *trusted, in-process* code with
  full backend access. Only load files you wrote or audited. This is the
  deliberate inverse of the sandboxed code interpreter.
- **Code interpreter (`FREE_WEBUI_CODE_INTERPRETER`)** — the `docker` backend is
  a real sandbox; the `subprocess` backend is **not** a security boundary and is
  for trusted single-user use only.
- **SSRF guard** — user-supplied MCP server URLs and image-backend result URLs
  are validated against an egress policy (`FREE_WEBUI_SSRF_*`). By default
  link-local/metadata and private/loopback ranges are blocked; relax via
  `FREE_WEBUI_SSRF_ALLOW_HOSTS` / `FREE_WEBUI_SSRF_BLOCK_PRIVATE` only when you
  understand the implication.
- **Cookie `Secure`** — set `FREE_WEBUI_COOKIE_SECURE=true` when serving over
  HTTPS. It defaults to `false` for plain-HTTP localhost development.

## Hardening checklist for production

- Set a fixed `FREE_WEBUI_SECRET_KEY`; never copy the example/compose value.
- Serve over HTTPS, set `FREE_WEBUI_COOKIE_SECURE=true` and
  `FREE_WEBUI_SECURITY_HSTS=true`.
- Keep `FREE_WEBUI_SSRF_BLOCK_PRIVATE=true` (the default) for multi-user
  instances; allowlist only the internal hosts you trust.
- Prefer the `docker` code-interpreter backend; avoid `subprocess` for
  multi-user deployments.
- Put the app behind a reverse proxy with its own rate limiting and request
  size limits in addition to the built-in ones.

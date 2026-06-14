# Contributing to free-webui

Thanks for your interest. free-webui is a **clean-room, MIT-licensed** rewrite —
the project's entire value proposition is that it carries no upstream code,
license, or branding. The contribution rules exist to protect that.

## The one hard rule: clean-room

**Do not copy code, assets, strings, or markup from `open-webui` or any other
non-MIT-compatible source.** You may study its UX and feature set and
re-implement independently — but everything you submit must be your own
independent work (or from a permissively-licensed source you can attest to).

Every pull request must affirm the following (a checkbox is provided in the PR
template):

> I attest that this contribution is my own independent work and contains no
> code, assets, or strings copied from open-webui or any other source whose
> license is not compatible with MIT.

## Dependencies

Keep the dependency list short. Any **new** dependency must be licensed
permissively (MIT / BSD / Apache-2.0 / ISC / similar) — GPL/AGPL/SSPL and other
copyleft or source-available licenses are not acceptable, because the project
ships inside commercial products. Note the new dependency and its license in
your PR description. (A CI license-compatibility gate is on the roadmap; until
then this is checked by review.)

## Quality bar

- **Keep diffs small and readable.** Prefer deleting code to adding it.
- **Match the surrounding style** — comment density, naming, idioms.
- **Tests:** add or update tests for behaviour you change. Run the backend suite
  locally: `cd backend && pytest -q`. CI runs lint + tests on every PR.
- **No secrets** in commits (the repo CI flags hard-coded secrets).
- For security-sensitive changes, see [`SECURITY.md`](./SECURITY.md).

## Development

- Backend: Python ≥ 3.11, FastAPI. `cd backend && pip install -r requirements.txt`.
- Frontend: Node ≥ 20, SvelteKit. `cd frontend && npm install`.
- See [`README.md`](./README.md) for the full quick start, and
  [`docs/`](./docs) for the roadmap, test plan, and integration plan.

## Reporting bugs / requesting features

Open an issue with a clear repro or use case. For vulnerabilities, **do not** open
a public issue — follow [`SECURITY.md`](./SECURITY.md).

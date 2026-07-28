# Contributing

## Running the tests

```bash
uv sync --group bench
uv run pytest -q
```

The suite runs against real MCP servers over stdio and local HTTP — no mocks,
no network egress. The `bench` group installs `tiktoken` and `rank-bm25` so
the fast, model-free parts of `benchmarks/` run too. The full model-backed
benchmark (`benchmarks/run_benchmark.py`) is a manual step — see
[Benchmarks](README.md#benchmarks) in the README to reproduce the numbers
there.

## PR titles

`.github/workflows/changelog.yml` parses your PR title into a `CHANGELOG.md`
entry on merge, so use a conventional-commit prefix:

| Prefix | Changelog section |
|---|---|
| `feat:` | Added |
| `fix:` | Fixed |
| `docs:` | Documentation |
| anything else | Changed |

Example: `feat: add Codex CLI support to the migration script`.

## Releasing

Version bumps go in their own PR off a `release/vX.Y.Z` branch — bump
`version` in `pyproject.toml`, nothing else. On merge to `main`,
`.github/workflows/release.yml` tags `vX.Y.Z`, pushes it, and creates the
GitHub Release from `CHANGELOG.md`. It skips silently if that tag exists, so
merging anything without a bump is a no-op.

Publishing to PyPI is in the same workflow but **off by default**: it runs
only when the repo variable `PYPI_AUTOPUBLISH` is `true` (Settings → Secrets
and variables → Actions → Variables). Turning it on requires PyPI-side setup
first — add `toolsieve` as a Trusted Publisher (GitHub, owner
`TJLSmith0831`, repo `toolsieve`, workflow `release.yml`). No API token is
stored in this repo.

PyPI versions cannot be overwritten or deleted. Publish a version by hand
(`uv build && uv publish`) and confirm `uvx toolsieve` resolves from the real
index before enabling the automatic path.

## Design docs

Plans, decisions, and specs live in `openspec/changes/` (see `AGENTS.md`),
not `docs/plans/`.

## Touching routing or scoring

Read [Benchmarks](README.md#benchmarks) first and reproduce the numbers
before and after your change:

```bash
uv sync --group bench
uv run --group bench python benchmarks/run_benchmark.py
uv run python benchmarks/render_results.py
```

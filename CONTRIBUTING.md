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

# Agent notes

- **No superpowers-family docs in this repo.** Plans, decisions, and specs live in `openspec/` (see `openspec/changes/`), not `docs/plans/` or other superpowers-skill conventions. Don't invoke `superpowers:executing-plans`, `superpowers:subagent-driven-development`, or write plan files outside `openspec/`.
- **Use the `/ponytail:ponytail` skill whenever coding** — keep changes minimal, reach for stdlib/existing code before new abstractions or dependencies, and favor the shortest correct diff.

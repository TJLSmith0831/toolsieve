# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.1] - 2026-08-01

### Changed
- Bump version to 0.3.1 and update the README version badge ([#24](https://github.com/TJLSmith0831/toolsieve/pull/24))

## [0.3.0] - 2026-08-01

### Added
- Make toolsieve pip/uvx-installable with a portable migrate flow ([#18](https://github.com/TJLSmith0831/toolsieve/pull/18))
- Support OAuth-authenticated downstream MCP servers ([#19](https://github.com/TJLSmith0831/toolsieve/pull/19))
- Add Devin support and project-scoped configs for every client that has one ([#20](https://github.com/TJLSmith0831/toolsieve/pull/20))

### Changed
- Add .env to .gitignore ([#17](https://github.com/TJLSmith0831/toolsieve/pull/17))

### Fixed
- Cut find_tools tokens per resolved lookup, not just per call ([#22](https://github.com/TJLSmith0831/toolsieve/pull/22))

## [0.2.1] - 2026-07-27

### Added

- Load credentials from a `.env` file next to the config, hot-reloaded like
  the config itself — no more shell-export-and-restart. A real exported
  variable always wins over `.env`, which only fills gaps.
  ([#6](https://github.com/TJLSmith0831/toolsieve/issues/6))
- Codex CLI (TOML) support in `scripts/setup_toolsieve.py`'s client-config
  migration, alongside Claude Code, Claude Desktop, Cursor, Windsurf, and
  VS Code. Reads via stdlib `tomllib`, writes via `tomlkit` so a hand-edited
  `~/.codex/config.toml` keeps its comments and formatting.
  ([#9](https://github.com/TJLSmith0831/toolsieve/issues/9))

### Documentation

- Document `scripts/setup_toolsieve.py`'s migration flow in the README.
  ([#8](https://github.com/TJLSmith0831/toolsieve/issues/8))
- Add `CONTRIBUTING.md`. ([#10](https://github.com/TJLSmith0831/toolsieve/issues/10))
- Add a CI status badge to the README. ([#11](https://github.com/TJLSmith0831/toolsieve/issues/11))

## [0.2.0] - 2026-07-26

### Added

- HTTP/SSE MCP server support alongside stdio. Transport is inferred from the
  config entry (`command` = stdio, `url` = remote), so entries stay
  copy-pasteable from client configs. ([#2](https://github.com/TJLSmith0831/toolsieve/pull/2))
- `${VAR}` expansion for auth in remote `url`/`headers` values, read from the
  environment toolsieve runs in. An unset variable fails startup with an
  error naming it rather than firing an unauthenticated request.
- One retry in each direction for remote servers — once at aggregation for a
  momentary connect blip, once at call time for a dead session — with no
  retry for stdio, since a bad command is deterministic.

## [0.1.0] - 2026-07-25

### Added

- Initial release: semantic tool routing across aggregated MCP servers via
  `find_tools`, `call_tool`, and `get_savings_report`.

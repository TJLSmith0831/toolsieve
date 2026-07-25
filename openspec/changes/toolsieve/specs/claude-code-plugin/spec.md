## ADDED Requirements

### Requirement: Claude Code plugin wrapper
The system SHALL provide a Claude Code plugin manifest that bundles the toolsieve MCP server for one-command installation in Claude Code, in addition to toolsieve working as a standalone MCP server for any MCP client.

#### Scenario: Installing via Claude Code
- **WHEN** a Claude Code user installs the toolsieve plugin
- **THEN** Claude Code SHALL have the toolsieve MCP server configured and available without manual MCP server configuration

### Requirement: Bundled setup skill
The plugin SHALL bundle a skill that configures toolsieve into the user's coding agent: locating that client's MCP config, migrating its stdio `mcpServers` entries into toolsieve's config, and registering toolsieve with the client.

#### Scenario: Migrating an existing MCP setup
- **WHEN** the setup skill runs against a client that already has stdio MCP servers configured
- **THEN** those servers SHALL be moved into toolsieve's config and toolsieve SHALL be registered with the client in their place, with a backup of every file modified and no changes applied before the user confirms

#### Scenario: Non-stdio servers are left alone
- **WHEN** the client's config also contains HTTP/SSE MCP servers
- **THEN** those entries SHALL be left untouched and reported to the user, since v1 aggregates stdio servers only

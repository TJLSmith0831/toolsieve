## ADDED Requirements

### Requirement: Claude Code plugin wrapper
The system SHALL provide a Claude Code plugin manifest that bundles the toolsieve MCP server for one-command installation in Claude Code, in addition to toolsieve working as a standalone MCP server for any MCP client.

#### Scenario: Installing via Claude Code
- **WHEN** a Claude Code user installs the toolsieve plugin
- **THEN** Claude Code SHALL have the toolsieve MCP server configured and available without manual MCP server configuration

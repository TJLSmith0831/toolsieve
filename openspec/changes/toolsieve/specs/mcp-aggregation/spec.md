## ADDED Requirements

### Requirement: Config-driven server aggregation
The system SHALL read a `mcpServers`-shaped config file listing downstream MCP servers and SHALL connect to each one to collect its published tool list.

#### Scenario: Aggregating a valid multi-server config
- **WHEN** toolsieve starts with a config file listing two or more reachable downstream stdio MCP servers
- **THEN** the aggregated tool catalog SHALL include every tool published by each reachable server

### Requirement: Per-server connection failure isolation
WHEN a configured downstream server fails to connect, the system SHALL log a warning, exclude that server's tools from the aggregated catalog, and continue aggregating the remaining configured servers.

#### Scenario: One of several configured servers is unreachable
- **WHEN** one downstream server in the config is unreachable and the others are reachable
- **THEN** the aggregated catalog SHALL contain the reachable servers' tools and SHALL NOT include the unreachable server's tools, and toolsieve SHALL continue running

### Requirement: Live config-file reload
The system SHALL watch the config file for changes and SHALL re-aggregate (reconnect to every configured server and re-pull tool lists) without requiring a toolsieve process restart.

#### Scenario: Adding a new downstream server while running
- **WHEN** a user appends a new server entry to the config file while toolsieve is running
- **THEN** the new server's tools SHALL become available in the aggregated catalog without restarting toolsieve

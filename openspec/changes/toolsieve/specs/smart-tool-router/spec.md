## ADDED Requirements

### Requirement: Semantic tool matching via find_tools
The system SHALL expose a `find_tools(query, k=3)` tool that embeds the query and matches it against each aggregated tool's own name and description — no hand-authored example utterances, no LLM-generated synthetic phrasing — returning up to k matches.

#### Scenario: Query matches an aggregated tool
- **WHEN** find_tools is called with a query relevant to an aggregated tool's name/description
- **THEN** the response SHALL include that tool among the top-k matches, with its owning server, description, and input schema

#### Scenario: Query matches nothing
- **WHEN** find_tools is called with a query that clears no aggregated tool's similarity floor
- **THEN** the response SHALL be an explicit empty match list with a message indicating no tool matched, rather than the top-k regardless of relevance

### Requirement: Tool call proxying via call_tool
The system SHALL expose a `call_tool(server, tool_name, args)` tool that forwards the invocation to the named downstream server and returns its result.

#### Scenario: Successful proxied call
- **WHEN** call_tool is invoked with a server and tool_name previously returned by find_tools
- **THEN** the system SHALL forward the call to that downstream server and return its real result

#### Scenario: Target server has failed
- **WHEN** call_tool is invoked against a server that is unreachable
- **THEN** the system SHALL return a clear error identifying the failing server, without affecting calls to other servers

### Requirement: Live token-savings reporting
The system SHALL report token savings (naive all-tools cost vs. actual routed cost) both per find_tools call and as a running session total via get_savings_report().

#### Scenario: Per-call savings metadata
- **WHEN** find_tools returns a response
- **THEN** the response SHALL include tokens_if_naive, tokens_actual, and saved_pct computed from the aggregated catalog's schema size

#### Scenario: Session-total savings report
- **WHEN** get_savings_report is called after one or more find_tools calls in the session
- **THEN** the response SHALL return the cumulative naive-vs-actual token totals and savings percentage for the session so far

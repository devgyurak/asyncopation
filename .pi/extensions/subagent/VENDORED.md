# Vendored: Pi subagent extension

Copied unmodified from `@earendil-works/pi-coding-agent` 0.85.1
(`examples/extensions/subagent/{index.ts,agents.ts}`, MIT license).
Pi has no built-in sub-agents; this extension adds the `subagent` tool.

Project agents live in `.pi/agents/` (generated from `.agents/agents/`). Call the tool with
`agentScope: "project"` (or `"both"`) so they are discovered.

Refresh after upgrading Pi:
`cp "$(npm root -g)/@earendil-works/pi-coding-agent/examples/extensions/subagent/"{index,agents}.ts .pi/extensions/subagent/`

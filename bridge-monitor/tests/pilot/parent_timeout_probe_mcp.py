"""Harmless stdio MCP endpoint for verifying a project-local Codex client override."""

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("Bridge Monitor parent timeout probe")


@mcp.tool()
def echo_deadline_probe(value: str) -> str:
    """Return the supplied probe marker unchanged."""
    return value


if __name__ == "__main__":
    mcp.run(transport="stdio")

"""Harmless project-local MCP capability for a live Claude isolation probe."""

from mcp.server.fastmcp import FastMCP


def main() -> None:
    server = FastMCP("Bridge Monitor Isolation Probe")

    @server.tool()
    def delegate_local_analysis(prompt: str) -> dict:
        """Return a fixed receipt without contacting Skynet or reading files."""
        return {"probe": "ok", "echo": prompt}

    server.run(transport="stdio")


if __name__ == "__main__":
    main()

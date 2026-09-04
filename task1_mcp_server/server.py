import logging

from mcp.server import MCPServer

logger = logging.getLogger(__name__)

mcp = MCPServer(
    "fde-assessment",
    log_level="INFO",
)


def main() -> None:
    logger.info("Starting MCP server using stdio transport")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

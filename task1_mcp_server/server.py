import logging
from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

logger = logging.getLogger(__name__)

CustomerId = Annotated[
    str,
    Field(pattern=r"^CUST-\d{5}$"),
]

mcp = MCPServer(
    "fde-assessment",
    log_level="INFO",
)


@mcp.tool()
def get_customer_record(customer_id: CustomerId) -> dict[str, str]:
    """Return a mock customer record for a valid customer ID."""

    logger.info("Looking up customer record: %s", customer_id)

    return {
        "customer_id": customer_id,
        "name": "Touqeer",
        "email": "xyz@example.com",
        "status": "inactive",
    }


def main() -> None:
    logger.info("Starting MCP server using stdio transport")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
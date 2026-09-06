import logging
import sys
from typing import Annotated

from mcp import MCPError
from mcp.server import MCPServer, ServerRequestContext
from mcp.server.context import CallNext, HandlerResult
from mcp.types import INVALID_PARAMS
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

CustomerId = Annotated[
    str,
    Field(pattern=r"^CUST-\d{5}$", strict=True),
]

RefundAmount = Annotated[
    float,
    Field(gt=0, strict=True),
]

RefundReason = Annotated[
    str,
    Field(min_length=10, strict=True),
]


class CustomerRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: CustomerId


class RefundInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: CustomerId
    amount: RefundAmount
    reason: RefundReason


async def validate_tool_arguments(
    ctx: ServerRequestContext,
    call_next: CallNext,
) -> HandlerResult:
    if ctx.method != "tools/call":
        return await call_next(ctx)

    params = ctx.params or {}
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}

    try:
        if tool_name == "get_customer_record":
            CustomerRecordInput.model_validate(arguments)
        elif tool_name == "trigger_refund":
            RefundInput.model_validate(arguments)
    except ValidationError as exc:
        logger.warning(
            "Rejected invalid arguments for %s: %d validation error(s)",
            tool_name,
            exc.error_count(),
        )

        raise MCPError(
            code=INVALID_PARAMS,
            message="Invalid tool arguments",
            data={"tool": tool_name},
        ) from None

    return await call_next(ctx)


mcp = MCPServer(
    "fde-assessment",
    log_level="INFO",
    middleware=[validate_tool_arguments],
)


@mcp.tool()
def get_customer_record(customer_id: CustomerId) -> dict[str, str]:
    """Return a mock customer record for a valid customer ID."""

    logger.info("Looking up customer record: %s", customer_id)

    return {
        "customer_id": customer_id,
        "name": "John Doe",
        "email": "xyz@example.com",
        "status": "active",
    }


@mcp.tool()
def trigger_refund(
    customer_id: CustomerId,
    amount: RefundAmount,
    reason: RefundReason,
) -> dict[str, str | float]:
    """Trigger a mock refund for a customer."""

    logger.info(
        "Triggering refund for %s, amount=%s",
        customer_id,
        amount,
    )

    return {
        "customer_id": customer_id,
        "amount": amount,
        "reason": reason,
        "status": "approved",
    }


def main() -> None:
    logger.info("Starting MCP server using stdio transport")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
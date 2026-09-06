import logging
import sys

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="MCP Security Gateway")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
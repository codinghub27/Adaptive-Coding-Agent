"""Health-check helpers for external dependencies (kept out of `main.py`)."""

import asyncio
import logging

from qdrant_client import AsyncQdrantClient

logger = logging.getLogger(__name__)


async def ping_qdrant(client: AsyncQdrantClient, timeout_s: float = 3.0) -> bool:
    """Check Qdrant connectivity with a cheap call, timing out after `timeout_s`.

    Returns True on success and False on any exception, including a timeout.
    Never raises and never logs the URL or exception message, only the
    exception class name.
    """
    try:
        async with asyncio.timeout(timeout_s):
            await client.get_collections()
        return True
    except Exception as exc:
        logger.warning("qdrant ping failed: %s", type(exc).__name__)
        return False

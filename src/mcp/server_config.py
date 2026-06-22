"""
Logging configuration for the ResumeRanker MCP server.

CRITICAL: In stdio transport, stdout is exclusively for MCP JSON-RPC frames.
Any log output to stdout corrupts the protocol stream. All logging MUST go
to stderr only — StreamHandler() uses sys.stderr by default.
"""

import logging
import os


def configure_logging():
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    # Silence noisy Azure SDK and HTTP client loggers in production
    for noisy in ["azure.core.pipeline", "azure.storage", "httpx", "httpcore"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

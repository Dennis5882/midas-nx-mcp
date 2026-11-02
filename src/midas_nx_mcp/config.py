"""Settings from the project's .env, located from this file, not the cwd.

The MCP client launches the server with a working directory of its own
choosing, so a relative ".env" would be looked up in the wrong place.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ("gen", "civil")


def _read_env_file() -> dict[str, str]:
    path = PROJECT_ROOT / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    return values


def setting(name: str, default: str = "") -> str:
    """An environment variable wins over .env."""
    return os.environ.get(name) or _read_env_file().get(name, default)


def mapi_key(product: str) -> str:
    if product not in PRODUCTS:
        raise ValueError(f"product must be one of {PRODUCTS}, got {product!r}")
    key = setting(f"MIDAS_MAPI_KEY_{product.upper()}")
    if not key:
        raise ValueError(
            f"MIDAS_MAPI_KEY_{product.upper()} is not set (Hint: fill it in .env)"
        )
    return key


def timeout() -> float:
    return float(setting("MIDAS_TIMEOUT", "20"))


def analysis_timeout() -> float:
    """/doc/ANAL alone gets a long timeout: a big model legitimately takes minutes."""
    return float(setting("MIDAS_ANALYSIS_TIMEOUT", "300"))


def output_dir() -> Path:
    """Where NX writes files this server asks for (CLAUDE.md rule 5).

    The path is resolved by NX, on whatever PC runs it. This server reads the
    file back from the same path, so it only works when NX runs here.
    """
    return Path(setting("MIDAS_OUTPUT_DIR", "C:/temp"))


def max_rows() -> int:
    """Rows a tool returns at most; the rest is counted, not sent."""
    return int(setting("MIDAS_MAX_ROWS", "200"))

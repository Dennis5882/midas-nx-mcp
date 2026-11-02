"""Settings from environment variables, or from a source checkout's .env.

Installed (``uvx midas-nx-mcp``), the MCP client passes settings as
environment variables. Run from a checkout, the project's .env is read too,
located from this file, not the cwd: the MCP client launches the server with
a working directory of its own choosing.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ("gen", "civil")


def _env_file() -> Path | None:
    """The checkout's .env; None when installed, where PROJECT_ROOT is site-packages' parent."""
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        return None
    return PROJECT_ROOT / ".env"


def _read_env_file() -> dict[str, str]:
    path = _env_file()
    values: dict[str, str] = {}
    if path is None or not path.exists():
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
            f"MIDAS_MAPI_KEY_{product.upper()} is not set (Hint: add it to the env of "
            "this server in the MCP client's settings, or to .env in a source checkout)"
        )
    return key


def timeout() -> float:
    return float(setting("MIDAS_TIMEOUT", "20"))


def analysis_timeout() -> float:
    """/doc/ANAL alone gets a long timeout: a big model legitimately takes minutes."""
    return float(setting("MIDAS_ANALYSIS_TIMEOUT", "300"))


def save_timeout() -> float:
    """save_copy only: writing a big model takes longer than a plain read."""
    return float(setting("MIDAS_SAVE_TIMEOUT", "120"))


def output_dir() -> Path:
    """Where NX writes files this server asks for (CLAUDE.md rule 5).

    The path is resolved by NX, on whatever PC runs it. This server reads the
    file back from the same path, so it only works when NX runs here.
    """
    return Path(setting("MIDAS_OUTPUT_DIR", "C:/temp"))


def max_rows() -> int:
    """Rows a tool returns at most; the rest is counted, not sent."""
    return int(setting("MIDAS_MAX_ROWS", "200"))

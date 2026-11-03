"""Build dist/midas-nx-mcp-<version>.mcpb, a one-click install for Claude Desktop.

The bundle is a thin wrapper around the PyPI package, pinned to this
checkout's version: the MCPB "uv" runtime has Claude Desktop install Python
and `midas-nx-mcp==<version>` itself, so the bundle carries no code but a
two-line launcher. The keys are asked for in Claude Desktop's install dialog
(stored as sensitive) and passed as the same environment variables as
`uvx midas-nx-mcp`.

Run after that version is on PyPI (a bundle of an unpublished version
installs nothing). Needs Node (npx) for the official `mcpb` packer.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

from midas_nx_mcp import __version__, server

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build" / "mcpb"
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"

LAUNCHER = """\
from midas_nx_mcp.server import main

main()
"""

PYPROJECT = """\
[project]
name = "midas-nx-mcp-bundle"
version = "{version}"
description = "Claude Desktop bundle of midas-nx-mcp {version}"
requires-python = ">=3.11"
dependencies = ["midas-nx-mcp=={version}"]
"""

USER_CONFIG = {
    "gen_key": {
        "type": "string",
        "title": "Gen NX MAPI key",
        "description": "From Apps > API Settings in Gen NX. Leave empty if you do not use Gen.",
        "sensitive": True,
        "required": False,
    },
    "civil_key": {
        "type": "string",
        "title": "Civil NX MAPI key",
        "description": "From Apps > API Settings in Civil NX. Leave empty if you do not use "
        "Civil. Gen and Civil keys differ; a swapped key still reports connected.",
        "sensitive": True,
        "required": False,
    },
    "output_dir": {
        "type": "directory",
        "title": "Output folder",
        "description": "Where NX writes captures and save_copy files. Must exist.",
        "default": "C:\\temp",
        "required": False,
    },
    "analysis_timeout": {
        "type": "number",
        "title": "Analysis timeout (s)",
        "description": "How long run_analysis waits. Raise it for big models.",
        "default": 300,
        "min": 30,
        "required": False,
    },
}

ENV = {
    "MIDAS_MAPI_KEY_GEN": "${user_config.gen_key}",
    "MIDAS_MAPI_KEY_CIVIL": "${user_config.civil_key}",
    "MIDAS_OUTPUT_DIR": "${user_config.output_dir}",
    "MIDAS_ANALYSIS_TIMEOUT": "${user_config.analysis_timeout}",
}


def manifest() -> dict:
    tools = asyncio.run(server.mcp.list_tools())
    return {
        "manifest_version": "0.4",
        "name": "midas-nx-mcp",
        "display_name": "MIDAS Gen NX / Civil NX",
        "version": __version__,
        "description": "Read the model open in MIDAS Gen NX or Civil NX, and change it only "
        "through previewed, confirmed steps.",
        "long_description": "A local MCP server built on the midas-nx SDK. Needs Gen NX or "
        "Civil NX running on this PC with its Open API connected. Personal project; "
        "not an official MIDAS IT product.",
        "author": {"name": "Dennis", "url": "https://github.com/Dennis5882"},
        "repository": {"type": "git", "url": "https://github.com/Dennis5882/midas-nx-mcp"},
        "homepage": "https://github.com/Dennis5882/midas-nx-mcp",
        "documentation": "https://github.com/Dennis5882/midas-nx-mcp#readme",
        "support": "https://github.com/Dennis5882/midas-nx-mcp/issues",
        "server": {
            "type": "uv",
            "entry_point": "server.py",
            "mcp_config": {
                "command": "uv",
                "args": ["run", "--directory", "${__dirname}", "server.py"],
                "env": ENV,
            },
        },
        "tools": [
            {"name": t.name, "description": (t.description or "").split("\n")[0]} for t in tools
        ],
        "user_config": USER_CONFIG,
        # Reads NX's window titles through the Win32 API; NX itself is Windows-only.
        "compatibility": {"platforms": ["win32"], "runtimes": {"python": ">=3.11"}},
        "keywords": ["midas", "gen nx", "civil nx", "structural", "engineering"],
        "license": "MIT",
    }


def main() -> None:
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    (STAGE / "manifest.json").write_text(json.dumps(manifest(), indent=2), encoding="utf-8")
    (STAGE / "pyproject.toml").write_text(PYPROJECT.format(version=__version__), encoding="utf-8")
    (STAGE / "server.py").write_text(LAUNCHER, encoding="utf-8")
    out = ROOT / "dist" / f"midas-nx-mcp-{__version__}.mcpb"
    out.parent.mkdir(exist_ok=True)
    npx = shutil.which("npx") or sys.exit("npx not found: install Node to pack the bundle")
    subprocess.run([npx, "-y", MCPB_CLI, "validate", str(STAGE / "manifest.json")], check=True)
    subprocess.run([npx, "-y", MCPB_CLI, "pack", str(STAGE), str(out)], check=True)
    print(out, file=sys.stderr)


if __name__ == "__main__":
    main()

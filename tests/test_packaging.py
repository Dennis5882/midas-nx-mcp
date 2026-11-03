"""The .mcpb manifest, server.json and README agree with the package."""

import asyncio
import json
import re
import runpy
from pathlib import Path

from midas_nx_mcp import __version__, server

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = set(re.findall(r"\b(MIDAS_[A-Z_]+)\b", (ROOT / ".env.example").read_text("utf-8")))


def _bundle_manifest() -> dict:
    return runpy.run_path(str(ROOT / "scripts" / "build_mcpb.py"))["manifest"]()


def test_the_bundle_lists_every_tool_and_known_settings():
    manifest = _bundle_manifest()
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {t["name"] for t in manifest["tools"]} == tools
    assert manifest["version"] == __version__
    env = manifest["server"]["mcp_config"]["env"]
    assert set(env) <= SETTINGS
    for value in env.values():
        option = value.removeprefix("${user_config.").removesuffix("}")
        assert option in manifest["user_config"], value


def test_server_json_matches_the_readme_and_settings():
    meta = json.loads((ROOT / "server.json").read_text("utf-8"))
    assert len(meta["description"]) <= 100  # the registry's limit
    # PyPI ownership: the README (the PyPI description) names the server.
    assert f"mcp-name: {meta['name']} " in (ROOT / "README.md").read_text("utf-8")
    names = {v["name"] for v in meta["packages"][0]["environmentVariables"]}
    assert names <= SETTINGS

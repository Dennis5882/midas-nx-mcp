"""Start the installed `midas-nx-mcp` command over stdio, as an MCP client would.

Run with the python of a venv the wheel was installed into, from outside the
checkout, so nothing is imported from the source tree. Needs no NX and no key.
Given arguments, starts that command instead, e.g. an unpacked .mcpb bundle:
``python wheel_smoke_test.py uv run --directory <bundle> server.py``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "check_connection", "list_tables", "describe_table", "read_table", "model_summary",
    "read_model_info", "list_result_tables", "result_table", "capture_view", "show_result",
    "run_analysis", "save_copy", "write_records", "delete_records", "generate_load_combinations",
}


async def main() -> None:
    if len(sys.argv) > 1:
        command, args = sys.argv[1], sys.argv[2:]
    else:
        command = shutil.which("midas-nx-mcp", path=str(Path(sys.executable).parent))
        assert command, "the midas-nx-mcp command was not installed next to this python"
        args = []
    # A bundle passes its empty, optional settings as empty variables.
    env = {"MIDAS_MAPI_KEY_GEN": "", "MIDAS_OUTPUT_DIR": "", "MIDAS_ANALYSIS_TIMEOUT": ""}
    for name in ("PATH", "SYSTEMROOT", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "TEMP"):
        if name in os.environ:
            env[name] = os.environ[name]
    params = StdioServerParameters(command=command, args=args, env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {tool.name for tool in (await session.list_tools()).tools}
        assert tools == EXPECTED_TOOLS, f"tools differ: {sorted(tools ^ EXPECTED_TOOLS)}"
        listed = await session.call_tool("list_tables", {"search": "node"})
        assert not listed.is_error and "NODE" in listed.content[0].text
        refused = await session.call_tool("check_connection", {"product": "gen"})
        assert refused.is_error and "MIDAS_MAPI_KEY_GEN" in refused.content[0].text
    print(f"ok: {len(tools)} tools over stdio from {command}")


asyncio.run(main())

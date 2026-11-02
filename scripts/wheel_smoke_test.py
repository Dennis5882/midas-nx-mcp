"""Start the installed `midas-nx-mcp` command over stdio, as an MCP client would.

Run with the python of a venv the wheel was installed into, from outside the
checkout, so nothing is imported from the source tree. Needs no NX and no key.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "check_connection", "list_tables", "describe_table", "read_table", "model_summary",
    "read_model_info", "list_result_tables", "result_table", "capture_view",
    "run_analysis", "write_records", "delete_records",
}


async def main() -> None:
    command = shutil.which("midas-nx-mcp", path=str(Path(sys.executable).parent))
    assert command, "the midas-nx-mcp command was not installed next to this python"
    params = StdioServerParameters(command=command, args=[], env={"MIDAS_MAPI_KEY_GEN": ""})
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

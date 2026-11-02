# midas MCP Server

A local MCP server that lets Claude Desktop read, and with a previewed and
confirmed step change, the model open in MIDAS Gen NX or Civil NX, through
the `midas-nx` SDK. Personal project; not a MIDAS IT product.

## Setup

```bash
cd "E:\AI Study\Small Project\midas MCP Server"
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env      # then fill in the two MAPI keys
```

MAPI keys are issued per product in each NX program. Keep `.env` out of git.

## Try it without Claude

```bash
npx @modelcontextprotocol/inspector .venv\Scripts\python.exe -m midas_nx_mcp.server
```

opens the MCP Inspector in a browser, where each tool can be called by hand.
(Needs Node. `mcp dev` also works but builds its own uv environment, so it
needs `-e .` to see this project and `midas-nx`.)

## Tools

Reading (nothing in the model changes):

| tool | calls NX? | what it returns |
| --- | --- | --- |
| `check_connection(product)` | yes | connected or not, program name |
| `list_tables(product?, search?)` | no | the `/db` and `/DESIGN` tables `midas-nx` knows (304) |
| `describe_table(product, table)` | yes | the server's field schema, `GET /info/db/<TABLE>` |
| `read_table(product, table, ids?, limit?)` | yes | records, capped at `MIDAS_MAX_ROWS` |
| `model_summary(product)` | yes | counts of nodes, elements, materials, sections, thicknesses, load cases; units |
| `read_model_info(product, what)` | yes | project status, section properties, story check parameters, current selection |
| `list_result_tables(search?)` | no | the `/post/TABLE` types `midas-nx` knows (133) |
| `result_table(product, table_type, load_cases?, components?, keys?, limit?)` | yes | one result table, nothing exported |
| `capture_view(product, width?, height?)` | yes | the NX model window as a PNG (written to `C:/temp`, read back, removed) |

Changing (each called twice: first a preview with a `confirm_token`, then the
same call with that token, applied only if nothing changed in between):

| tool | what it does |
| --- | --- |
| `run_analysis(product)` | `/doc/ANAL`. NX saves the open file first; refused for a never-saved document |
| `write_records(product, table, records, mode)` | create or update up to 50 `/db` records, then reads the table back |
| `delete_records(product, table, ids)` | delete up to 50 records by id; nodes still used by elements are refused |

`product` is `"gen"` or `"civil"`, and picks that product's key.

## Register in Claude Desktop

Open Settings > Developer > Edit Config (not "Add custom connector", which is
for remote HTTPS servers). The file is `%APPDATA%\Claude\claude_desktop_config.json`,
or for the Microsoft Store install
`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`.
Add at the top level:

```json
{
  "mcpServers": {
    "midas-nx": {
      "command": "E:\\AI Study\\Small Project\\midas MCP Server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "midas_nx_mcp.server"]
    }
  }
}
```

Restart Claude Desktop. The tools appear under the tools icon in the chat box.

## Safety

Reads never change the model. Changes need a preview and its token, and are
read back afterwards. See `CLAUDE.md` for the rules and `PLAN.md` for what
comes next.

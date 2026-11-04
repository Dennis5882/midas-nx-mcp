# midas-nx-mcp

A local [MCP](https://modelcontextprotocol.io) server that lets Claude (and other
MCP clients) read the model open in **MIDAS Gen NX** or **MIDAS Civil NX**, and
change it only through steps you see and confirm first. Built on the
[`midas-nx`](https://github.com/Dennis5882/MIDAS-API-NX-SDK) SDK.

Built by a MIDAS IT team member as a personal project; **not an official MIDAS IT product**.

> Status: alpha. Verified live on Gen NX 2026 and Civil NX 2026 on small test
> models (see `PLAN.md`); not yet used on large production models.

사용 설명서 (한국어): [`docs/user_guide_ko.md`](https://github.com/Dennis5882/midas-nx-mcp/blob/main/docs/user_guide_ko.md)
· 소개 페이지: <https://dennis5882.github.io/midas-nx-mcp/>
· AI용 요약: [`llms.txt`](https://dennis5882.github.io/midas-nx-mcp/llms.txt)

<!-- mcp-name: io.github.Dennis5882/midas-nx-mcp -->

## What you can ask

- "Summarise the Gen model." - node, element, material, section, load case counts
- "Show the DL reactions." - result tables after an analysis
- "Show me the model." - a picture of the NX window
- "Which design code is set?" - design parameter tables
- "Add a node at (4, 3, 0)", "Run the analysis" - previewed, applied only after you agree

## Requirements

- Windows, with Gen NX or Civil NX running **on the same PC** and its Open API
  connected (Apps > API Settings)
- A MAPI key per product (Apps > API Settings in each program). Keys differ
  between Gen and Civil, and a wrong key still reports "connected", so do not
  swap them.
- [uv](https://docs.astral.sh/uv/) (installs Python and the server for you);
  not needed for the Claude Desktop bundle below

## Install

### Claude Desktop - one click

Download `midas-nx-mcp-<version>.mcpb` from the
[latest release](https://github.com/Dennis5882/midas-nx-mcp/releases/latest)
and open it (or drag it onto Settings > Extensions). Claude Desktop installs
Python and the server itself, and asks for your Gen and Civil keys; leave the
one you do not use empty. Change them later under Settings > Extensions.

### Claude Desktop - by hand

Settings > Developer > Edit Config (not "Add custom connector": that is for
remote servers) and add:

```json
{
  "mcpServers": {
    "midas-nx": {
      "command": "uvx",
      "args": ["midas-nx-mcp"],
      "env": {
        "MIDAS_MAPI_KEY_GEN": "your Gen NX key",
        "MIDAS_MAPI_KEY_CIVIL": "your Civil NX key"
      }
    }
  }
}
```

Quit Claude Desktop completely (tray icon too) and start it again. Settings >
Developer should show `midas-nx` as running.

### Claude Code

```bash
claude mcp add midas-nx -e MIDAS_MAPI_KEY_GEN=your-gen-key -- uvx midas-nx-mcp
```

### Other clients (Codex, ...)

Add a STDIO server with command `uvx`, argument `midas-nx-mcp`, and the
environment variables below.

## Settings

| variable | default | meaning |
| --- | --- | --- |
| `MIDAS_MAPI_KEY_GEN` | - | Gen NX key. Needed for `product="gen"` |
| `MIDAS_MAPI_KEY_CIVIL` | - | Civil NX key. Needed for `product="civil"` |
| `MIDAS_TIMEOUT` | `20` | seconds per call. Short on purpose: a dialog in NX blocks the API |
| `MIDAS_ANALYSIS_TIMEOUT` | `300` | seconds for `run_analysis` only |
| `MIDAS_SAVE_TIMEOUT` | `120` | seconds for `save_copy` only |
| `MIDAS_MAX_ROWS` | `200` | most rows one call returns; the rest are counted, not sent |
| `MIDAS_OUTPUT_DIR` | `C:/temp` | where NX writes captures. Must exist |

## Tools

Reading (nothing in the model changes):

| tool | what it returns |
| --- | --- |
| `check_connection(product)` | connected or not, program name |
| `list_tables(product?, search?)` | the `/db` and `/DESIGN` tables `midas-nx` knows |
| `describe_table(product, table)` | NX's own field schema for a `/db` table |
| `read_table(product, table, ids?, limit?)` | records, capped at `MIDAS_MAX_ROWS` |
| `model_summary(product)` | counts of the main tables, and the unit system |
| `read_model_info(product, what)` | project status, section properties, story check parameters, current selection |
| `list_result_tables(search?)` | the result table types `result_table` accepts |
| `result_table(product, table_type, load_cases?, components?, keys?, limit?)` | one result table; nothing exported |
| `capture_view(product, width?, height?)` | the NX window as a PNG |

Changing - each is called twice: first a preview with a `confirm_token`, then
the same call with that token. It applies only if nothing changed in between,
and reads the table back afterwards:

| tool | what it does |
| --- | --- |
| `run_analysis(product)` | runs the analysis. NX saves the open file first; a never-saved document is refused. A dialog NX opens meanwhile is reported at once |
| `save_copy(product)` | Save As to a generated name in `MIDAS_OUTPUT_DIR`; NX then works on the copy and the original stays as last saved. Gives a new document a file |
| `write_records(product, table, records, mode)` | creates or updates up to 50 `/db` or `/DESIGN` records |
| `delete_records(product, table, ids)` | deletes up to 50 records by id; nodes still used by elements are refused; a `/DESIGN` record NX resets instead of removing is reported as such |

Any write discards the analysis results in NX (even one that stores the same
value); run the analysis again before reading results.

Never exposed: new/open/import document, emptying a table.

## Development

```bash
git clone https://github.com/Dennis5882/midas-nx-mcp
cd midas-nx-mcp
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env      # a checkout reads its .env; an installed package does not
pytest
ruff check src tests scripts
npx @modelcontextprotocol/inspector .venv\Scripts\python.exe -m midas_nx_mcp.server
```

`AGENTS.md` holds the safety rules every tool follows, and `PLAN.md` what has
been verified live and what is open.

## Contact

Bugs and ideas for this server go to
[GitHub Issues](https://github.com/Dennis5882/midas-nx-mcp/issues); questions about
the products, licensing or the Open API service itself go to MIDAS IT's official
support channels. For anything else, the author is Dennis:
[sjj0507@midasit.com](mailto:sjj0507@midasit.com) or [LinkedIn](https://www.linkedin.com/in/dennis58).

## License

MIT

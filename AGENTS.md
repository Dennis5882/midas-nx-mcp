# AGENTS.md — midas MCP Server

A **local MCP server** that lets Claude Desktop (and any other MCP client on
this PC) read from, and later carefully write to, a running **MIDAS Gen NX** or
**MIDAS Civil NX** session through the MIDAS NX Open API.

It is a *consumer* of the published `midas-nx` package (PyPI), not part of it.
The SDK lives in `E:\AI Study\MIDAS-API-NX-SDK` (repo `Dennis5882/MIDAS-API-NX-SDK`);
do not edit that repository from here, and do not vendor its source. Depend on
the released package, so this project exercises exactly what users install.

## Commands

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"          # mcp[cli] 2.x, midas-nx, pytest, ruff, build, twine
pytest                           # offline tests; no NX needed
ruff check src tests scripts
npx @modelcontextprotocol/inspector .venv\Scripts\python.exe -m midas_nx_mcp.server
```

The server runs over **stdio** and is launched by the MCP client (see README).
Installed, it is the `midas-nx-mcp` command (`uvx midas-nx-mcp`) and takes its
settings from environment variables only; a source checkout also reads `.env`.

## Releasing

GitHub `Dennis5882/midas-nx-mcp` (public) -> PyPI `midas-nx-mcp`, the same way
as `midas-nx`: bump `__version__` in `src/midas_nx_mcp/__init__.py`, commit,
push, then publish a GitHub Release tagged `v<version>`. `publish.yml` checks
the tag against `__version__`, tests, builds, starts the wheel over stdio
(`scripts/wheel_smoke_test.py`) and uploads by Trusted Publishing (environment
`pypi`). A version on PyPI cannot be replaced, so never reuse a number.
After PyPI, the same workflow builds the Claude Desktop bundle
(`scripts/build_mcpb.py`: a `.mcpb` that pins `midas-nx-mcp==<version>` and
runs it on the MCPB "uv" runtime), starts it, attaches it to the release, and
lists both in the MCP Registry as `io.github.Dennis5882/midas-nx-mcp`
(`server.json`, filled with the version and the bundle's hash at release;
the README's `mcp-name:` line proves the PyPI package is ours - keep it).

Every release gets notes, written without being asked (`gh release create
--notes-file`, never empty or only the auto-generated commit list): what
changed for the user since the last tag, grouped by tool, why (what was seen
live, if anything), and the closing line ``Upgrade: `uvx midas-nx-mcp@latest`.``
Build them from `git log v<previous>..HEAD` and PLAN.md. In English, like the
README.

## How this project is laid out

- `src/midas_nx_mcp/server.py` — the `MCPServer` (mcp 2.x; v1 called it
  FastMCP) and every tool. Keep tools thin: argument checks and safety rules
  here, API work in `midas-nx`. Every tool goes through `_guarded`: mcp 2.x
  hides the text of any exception that is not a `ToolError`, which would
  swallow NX's own message.
- `src/midas_nx_mcp/catalog.py` — the `/db` and `/DESIGN` tables and the
  `/post/TABLE` types, discovered from `midas-nx`'s classes and constants, not
  listed by hand.
- `src/midas_nx_mcp/nx_window.py` — the open document's path, read from the
  NX window title (the API has no call for it); `run_analysis` refuses
  without one.
- `src/midas_nx_mcp/config.py` — reads `.env` from the project root (found from
  `__file__`, never the working directory - the MCP client sets that).
- `tests/` — offline tests: a real `MidasClient` over a scripted HTTP session
  (`conftest.py`), so the SDK's own error handling is exercised. The fixture
  fails any test that changes the model unless it set `writes_allowed`, and
  every test that sends a whole-table DELETE or `/doc/NEW|OPEN|IMPORT*`.
- `site/` — the public page (GitHub Pages, `pages.yml` on push to main):
  `index.html` for people, `llms.txt` for AI agents, in Korean at the root
  and in English under `en/`, with `assets/` shared. They repeat the README
  and the Korean guide; when a tool, setting or install step changes, update
  them too. Only `site/` is published.
- `PLAN.md` — phases, what each tool may do, and what is still open.
- `docs/mcp_background_ko.md` — why this project exists and why it is a local
  stdio server (the author's notes, in Korean): when MCP is needed at all,
  local vs remote, language choice.

## Rules that are not optional

These come from real incidents in the SDK project; each one has cost a model or
a blocked session there.

1. **stdout belongs to the protocol.** A stdio MCP server that `print()`s
   corrupts the JSON-RPC stream. Log with `logging` to stderr only.
2. **Keys are per product.** `.env` holds `MIDAS_MAPI_KEY_GEN` and
   `MIDAS_MAPI_KEY_CIVIL`. A wrong key still answers `verify_connection()` as
   "connected", so every tool takes a `product` and uses that product's key.
   Never log, return or echo a key.
3. **Reads stay reads; every change is previewed.** Read tools send GET only,
   plus `/post/TABLE` without `EXPORT_PATH`, `/view/CAPTURE` into the
   output folder, and `/view/RESULTGRAPHIC` (`show_result`: it changes what
   the NX window shows, never the model or its results). The only tools that change the model or its file are
   PLAN.md's phase 2 tools (`run_analysis`, `save_copy`, `write_records`,
   `delete_records`), and each needs the `confirm_token` its own preview
   returned. A new write tool follows the same pattern: preview, token from
   the request and current state, apply, read back.
4. **Never expose these, in any phase, as a tool:** `/doc/NEW` (discards the
   open document; has crashed Gen NX on a large model), `/doc/OPEN`,
   `/doc/IMPORT*`, a whole-table `DELETE /db/<NAME>`, `delete_all()`.
5. **Every path belongs to the NX machine.** Saves and exports resolve on the
   PC running NX. A path that does not exist there pops a modal dialog that
   blocks the whole API session while the HTTP call still answers like a
   success. When saving is added, the only allowed directory is `C:/temp`
   (or one named in `.env`), file names are generated, and extensions are
   Gen `.mgbx`, Civil `.mcbz`.
6. **A 200 is not success.** `midas-nx` raises `MidasResultError` on an
   `{"error": ...}` body, but some failures arrive as a plain `message`
   ("... path is wrong", "Analysis failed", "error status"). Return the
   server's text to the model as is; never report "done" because nothing raised.
7. **A modal dialog in NX blocks everything.** Give every call a short timeout
   and, on a timeout, tell the user to look at the NX window, not to retry.
8. **Other apps share this NX host** and leave `sms_*`, `sf_*`,
   `gen_rc_temp_*` files in `C:/temp`. Do not touch them.
9. **Windows consoles are cp949.** Keep exception text ASCII.

## Domain notes worth knowing (from the SDK's live verification)

- `/post/TABLE`'s top-level response key is unstable (`TABLE_NAME`,
  `"Result Table"`, `"empty"`); use `midas_nx.post.base.unwrap_table()`.
- POST keys are renumbered by some tables (`/db/NLLP`, `/db/HPCE`, `/db/IEHP`,
  ...): the response echoes the key you sent, not the id stored. Read back.
- `/db/PHGE`'s Assign key is the element, not a sequence number.
- `/info/db/<NAME>` returns the server's own JSON schema for a `/db` table and
  is safe to call; it is the best way to show the model what a table holds.
- The SDK's `docs/live_verification_notes.md` is the long-form record of what
  the products actually do. Read the relevant section before adding a write tool.

## Conventions

- Reply to the author in Korean, tersely.
- Commit only when asked; imperative subject, body explaining why.

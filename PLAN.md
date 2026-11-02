# Plan — midas MCP Server

> Last updated: 2026-11-02 — phase 1 verified live on Gen NX (12-node test
> model) by calling each tool directly and over stdio from an unrelated cwd.
> Civil not yet (no key). Claude Desktop answers Gen questions through it.
> Phase 1b (capture, design tables, model info) verified live on Gen. Phase 2
> tools written at the author's request ahead of the "use phase 1 for a while"
> plan; offline-tested and previewed live, not yet applied live.

## Goal

Ask Claude Desktop in plain language about the model open in Gen NX or
Civil NX ("how many nodes?", "show the reactions for DL", "which sections are
used?") and, later, make small, reversible edits - with the safety rules in
CLAUDE.md enforced by the server, not left to the model's judgement.

Personal use, free: a local stdio server on this PC. No hosting.

## Architecture

```
Claude Desktop ──stdio(JSON-RPC)──▶ midas-nx-mcp (this project, local Python)
                                        │  midas-nx (PyPI)
                                        ▼
                         moa-engineers.midasit.com (MIDASIT relay)
                                        │
                                        ▼
                               Gen NX / Civil NX on this PC
```

## Phase 1 — read-only (start here)

| tool | what it does | API |
| --- | --- | --- |
| `check_connection(product)` | connected? which program? (no key, no email in the result) | `verify_connection()` |
| `list_tables(product?, search?)` | the `/db` table names the server knows, with titles | static list from `midas-nx` resources |
| `list_result_tables(search?)` | the `/post/TABLE` types `result_table` accepts | static list from `midas-nx` constants |
| `describe_table(product, table)` | the server's own schema for a table | `GET /info/db/<TABLE>` |
| `read_table(product, table, ids?)` | records of one table, optionally a few ids | `GET /db/<TABLE>[/<id>]` |
| `model_summary(product)` | counts of nodes, elements, materials, sections, load cases | several GETs |
| `result_table(product, table_type, load_cases?, components?)` | a result table after analysis | `/post/TABLE`, **no** `EXPORT_PATH` |

Done when:
- [x] `pip install -e ".[dev]"` works; `pytest` and `ruff` pass
- [x] the server starts over stdio and lists its tools to a real MCP client
- [x] every phase 1 tool tried against an open Gen model (direct + stdio)
- [ ] the same against an open Civil model
- [ ] registered in Claude Desktop and answered a question on each product
      (Gen: "summarise the model" answered from several tools; "DL reactions"
      before analysis relayed NX's "no analysis result" as a failure; after the
      analysis, "show reactions" returned the same values as the direct call and
      the model spotted a missing-support error from CONS + geometry. Civil: not yet)
- [x] an analysis result table read live: REACTIONG and DISPLACEMENTG for
      "DL(ST)" after a by-hand analysis in Gen NX
- [x] a refused table name / a wrong product / a missing key each give a clear message
- [x] a result table without analysis returns NX's own text ("no analysis result")
- [ ] NX not running, and a dialog-blocked session (timeout message), seen live
- [x] output size is capped (large tables are truncated with a note, not dumped)
- [ ] `result_table` never passes `opt_cs`: it switches NX's construction-stage
      view, a state change. Revisit if CS results are needed.

## Phase 1b — more reads

| tool | API | live on Gen |
| --- | --- | --- |
| `capture_view` | `/view/CAPTURE` with `EXPORT_PATH`, `WIDTH`, `HEIGHT` only (no angle/display: they could change the on-screen view) | 1200x800 PNG in 1.8 s; file removed |
| `read_table` / `list_tables` on `/DESIGN/*` (79 tables) | GET | DSTL, RC DCTL read (empty: no design code) |
| `describe_table` on `/DESIGN/*` | refused: `/info/DESIGN/...` is 404 on both products (SDK notes) | - |
| `read_model_info` | GET `/ope/PROJECTSTATUS`, `SECTPROP`, `STORY_PARAM`, `STORY_IRR_PARAM` (Gen only), `/view/SELECT` | all five answered |

Left out on purpose: `/post/STEELCODECHECK` (has held a whole Gen session
behind a dialog), `*-ANAL` design runs (state changes; crash history on
Civil), `/post/PM` (response size and shape unknown), `/ope/STORYPROP`
(a POST query; not needed yet).

## Phase 2 — changes, each previewed and confirmed

Every tool runs twice: without `confirm_token` it changes nothing and returns
a preview and a token hashed from the request and the current state; with the
token it re-checks and applies only on a match, then reads back.

| tool | guard |
| --- | --- |
| `run_analysis` | document path from the NX window title; refused if never saved or not visible here (an unsaved document raises a blocking Save As). Preview warns that NX saves over the file first (`.bak`). Timeout `MIDAS_ANALYSIS_TIMEOUT` (300 s) reported as "may still be running, do not rerun" |
| `write_records` | `/db` only (not `/DESIGN`); create refuses existing ids, update missing ones; 50 records max; reports added/changed/removed from a full read-back, and flags renumbered ids and "answered but nothing changed" |
| `delete_records` | per-id DELETE only; nodes used by elements refused; NODE/ELEM counts compared before and after to catch cascades |

Live (2026-11-03, Gen, from Claude Desktop): previews; a node created and
deleted again (NODE back to 12); `run_analysis` applied twice. Gen stopped
both times on a modal **"Check Changed Story Information"** (story data no
longer matching the model). The first time the call simply waited on it. Now
`run_analysis` watches for windows of the NX process that were not there
before the call, and returns `waiting_for_nx_dialog` with the title at once;
measured: the dialog is a top-level window of `GenNX.exe` (a watcher logged
it appear at 00:02:23 and close at 00:03:08), and Claude Desktop relayed it
before the user answered.

- [x] apply live on the author's test model, with the author's go-ahead
      (write, delete, analysis; update not yet)
- [ ] write `/DESIGN` tables? (RC `DCO` has been refused, and Civil
      `CD-ANAL` without an RC code crashed NX twice: see SDK notes)
- [ ] `save_copy` via `/doc/SAVEAS`: Save As in the UI re-points the open
      document at the copy, so later saves would go there. Not measured
      through the API; check the SDK notes before building it.

Never: see CLAUDE.md rule 4.

## Open questions

- Does Claude Desktop on the author's plan allow local MCP servers?
  (Settings > Developer. Check before building much.)
- Distribute? If ever shared, publish to PyPI as `midas-nx-mcp` and run with
  `uvx`; for now, local only.
- One server per product, or one server with a `product` argument? Starting
  with one server and an argument.

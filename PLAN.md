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
AGENTS.md enforced by the server, not left to the model's judgement.

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
- [x] the same against an open Civil model. 2026-11-03, on an empty, never-saved
      Civil document: connection, every read tool, capture (PNG), "no analysis
      result" relayed, `run_analysis` refused for the unsaved document, and a
      NODE create -> update -> token replay refused -> delete round trip, back
      to 0 nodes. Then a dummy model built entirely with `write_records`
      (MATL, SECT, NODE, ELEM, CONS, STLD, BODF; the SDK's live_smoke column:
      0.6 x 0.6 m C24, 3.2 m, fixed base), saved to
      `C:/temp/mcp-dummy-civil-20261103-002739.mcbz` with the SDK's `save_as`,
      `run_analysis` done in 12.6 s, DL reaction FZ 27.113 kN = 0.36 m2 x 3.2 m
      x 23.54 kN/m3, the same C24 unit weight as the Gen model
- [ ] registered in Claude Desktop and answered a question on each product
      (Gen: "summarise the model" answered from several tools; "DL reactions"
      before analysis relayed NX's "no analysis result" as a failure; after the
      analysis, "show reactions" returned the same values as the direct call and
      the model spotted a missing-support error from CONS + geometry. Civil: not yet)
- [x] an analysis result table read live: REACTIONG and DISPLACEMENTG for
      "DL(ST)" after a by-hand analysis in Gen NX
- [x] a refused table name / a wrong product / a missing key each give a clear message
- [x] a result table without analysis returns NX's own text ("no analysis result")
- [ ] NX not running, and a dialog-blocked session (timeout message), seen live.
      2026-11-03, Gen: a dialog the *user* opened does not block the API -
      with "Material Data" open, and again with File > Save As open,
      `check_connection`, `read_table` and `model_summary` answered in 0.4-0.8 s
      and the window watch listed the dialog. The blocking seen so far was a
      dialog NX raised *during* an API call (analysis, Save As of an unsaved
      document). A write with a user dialog open was not tried.
      NX closed (Gen, same day): verify answers "disconnected", a read
      "404: client does not exist" (reads like a wrong id), and `run_analysis`
      said "run it in NX (F5)". Now, when the product has no window on this
      PC, every error and `check_connection` add "NX is not running ... or it
      runs on another PC". Restarted, Gen shows "[Start Page]" with no project
      ("The project is not opened" on a read); `run_analysis` and `save_copy`
      took that for a never-saved document and now say "no project is open".
      The start page is told by the title's shape (one bracket, not two), not
      its words. Civil's start page, seen live: "MIDAS CIVIL NX - [Start Page]"
      (no year, unlike its document titles); `run_analysis` and `save_copy`
      refused it as "no project is open", a read got "The project is not
      opened". (The first close only left an untitled document.) On Gen's start page,
      the SDK's `doc.new_project()` (called directly, not a tool) answered
      "command complete" in 6.1 s and opened "[제목 없음]".
      Still to see: the timeout message itself
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
| `show_result` | `/view/RESULTGRAPHIC` (mode, load case, one component, display types), then `/view/CAPTURE` as above | 2026-11-04, see below |
| `list_result_tables` by category | no NX call | the "story" note: see below |

`show_result`, live on Gen NX 2026 (2026-11-04), on a `save_copy` of the
author's 6-story RC building (3137 nodes, 24 load cases), analysed by
`run_analysis` in 70 s; the original file was not touched.

- There is no `/info/view/RESULTGRAPHIC` (404) and the manual names three
  modes only, so the modes and components were found by trying them.
  `reactionforces/moments` (FX..MZ), `displacementcontour` and `deformedshape`
  (DX, DY, DZ, DXYZ), `beamdiagrams` (Fx, Fy, Fz, Mx, My, Mz) and
  `plateforces/moments` (Fxx..Mxy): NX drew and captured every pair, 5-8 s
  each through the tool. The captures were looked at, not only the answers:
  NX answers "command complete" to a mode with nothing switched on in
  `TYPE_OF_DISPLAY` and draws nothing, so the tool always turns on the legend
  and the contour, deform or values that suit the mode.
- NX also took the spaced "displacement contour"; the tool sends the
  compact names.
- Load combinations: `gLCB1` as type `CB` and `cLCB1` as `CBC` both drew.
- Refusals are quick and leave the session alive: an unknown mode or
  component answers "second query is wrong" (0.4 s), an unknown load case
  "Can not find load case".
- The deformed shape comes out with scale factor 0 (nothing to see) unless
  `DEFORM.SCALE_FACTOR` is sent; `deform_scale` sends it.
- Civil NX: the route answers (SDK notes), the modes were not tried.

Story tables: on that model (stories defined, no story calculation) every
story table answered "no analysis result" before the analysis, as the SDK
notes say; `result_table` now adds what NX needs. STORY_MASS answered with
rows (it is model data).

After the analysis, the first story table asked for (STORY_DRIFT_X) held
the session past the 20 s timeout, and the two calls behind it timed out
too; no dialog window was open. A minute later NX answered again, and from
then on every story table answered within 1.5 s: STORY_DRIFT_X "Unknown
Error", the others "no analysis result" (no story calculation in this
model), BEAMDESIGNFORCES an empty table. Seen once; the timeout text did
its job, so the timeout stays as it is until this repeats.

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
| `save_copy` | `/doc/SAVEAS` to `MIDAS_OUTPUT_DIR/mcp-save-{product}-{time}` (`.mgbx` Gen, `.mcbz` Civil) only; NX window must be visible; success is the file on disk (Civil answers "command complete" for a save it did not write), and the window title is checked to name the copy (retried, with or without extension). Its own timeout (`MIDAS_SAVE_TIMEOUT`, 120 s), 30 s for the file to land, and the same new-window watch as `run_analysis`; a missing file is reported with "look at the NX window" and the windows open. Token covers the output folder |
| `write_records` | `/db` and `/DESIGN`; create refuses existing ids, update missing ones (except record 1 of the /DESIGN singletons DCO, DCTL, LLRF, LMRR, SRDF, listed by hand, where the first PUT creates it; 0.2.0 exempted every table without POST, fixed in 0.2.1 after review); 50 records max; reports added/changed/removed from a full read-back, and flags renumbered ids and "answered but nothing changed". Every preview warns that applying discards the analysis results; `/DESIGN` previews add the shared-record / DCO-first / Civil-refusal notes |
| `delete_records` | per-id DELETE only; nodes used by elements refused; NODE/ELEM counts compared before and after to catch cascades; a record still there afterwards is reported as reset by NX or not deleted |

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
      (write, delete, analysis on Gen; update on Civil)
- [x] write `/DESIGN` tables. Live on the Civil dummy (2026-11-03): RC `DCTL`
      created by PUT on a fresh document, read back identical through steel's
      `DCTL` (one shared record), the DL reaction then answered "no analysis
      result", and the DELETE left the record reset to `bAUTOKF false`,
      `DT 3D`, reported as "reset by NX". Same on Gen (an empty, never-saved
      document first given a file by `save_copy`; shared record seen through
      SRC's `DCTL`). The design runs (`*-ANAL`, crash history) stay out.
- [x] `save_copy` via `/doc/SAVEAS`. Live on the Civil dummy: a 159 KB
      `mcp-save-civil-...mcbz` written, the window title moved to the copy
      (so Save As does re-point the open document through the API too), and
      the analysis results survived the save.

`generate_load_combinations` (`/ope/LCOM-CONC|STEEL|SRC`, OPTION ADD and
DGNCODE only, as the SDK verified on 2026-10-26): live on the same copy
(2026-11-04). Concrete, with 252 combinations already there, added a full
second set of 284 (cLCB253...) in 1.9 s and changed none; steel, into an
empty LCOM-STEEL, added 284 (sLCB1 "1.4(D)", ...). The 24 load cases (D, L,
W and seismic types) are what makes it 284. The first reply carried every
record (200 KB); the tool now sends id, name, type and description, capped
at MIDAS_MAX_ROWS. Not offered: OPTION REPLACE (removes every existing
combination first), LCOM-GEN ("Wrong Field" on both schemas, SDK), Civil
(the routes are 404). Still to do from the same list: `/ope/LINEBMLD` (node
defined load line only; selected elements answer "Wrong Field") and
`/ope/DIVIDEELEM`; design checks wait for a dialog-safe way to run them.

Never: see AGENTS.md rule 4.

## Open questions

- Does Claude Desktop on the author's plan allow local MCP servers?
  (Settings > Developer. Check before building much.)
- Distribution: public on GitHub (`Dennis5882/midas-nx-mcp`) and PyPI
  (`midas-nx-mcp`, run with `uvx`), released from GitHub Releases. v0.1.0 on
  PyPI 2026-11-03; the author connected Codex to it with `uvx midas-nx-mcp`
  and the keys as env, and it worked (second client after Claude Desktop). Next: a
  `.mcpb` for Claude Desktop users who should not edit JSON, as a thin
  wrapper around the PyPI package (check the current MCPB spec for Python).
- One server per product, or one server with a `product` argument? Starting
  with one server and an argument.

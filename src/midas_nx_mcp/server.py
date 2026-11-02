"""The MCP server: read tools (phase 1) and previewed writes (phase 2, see PLAN.md).

Never print(): stdout carries the MCP protocol. Log to stderr.
"""

from __future__ import annotations

import difflib
import functools
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Literal

import requests
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from midas_nx import MidasAPIError, MidasClient, MidasConnectionError, doc, ope, view
from midas_nx.post.base import get_table, unwrap_table
from midas_nx.view import capture

from midas_nx_mcp import catalog, config, nx_window

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("midas_nx_mcp")

mcp = MCPServer(
    "midas-nx",
    instructions=(
        "Access to the model open in MIDAS Gen NX (product='gen') or Civil NX "
        "(product='civil'). Start with check_connection. Use list_tables and "
        "describe_table before read_table; list_result_tables before result_table. "
        "capture_view shows the NX model window as an image. "
        "run_analysis, save_copy, write_records and delete_records change the model or its "
        "file: call them first without confirm_token, show the preview to the user, "
        "and apply with the returned confirm_token only after the user agrees. "
        "If a call times out, ask the user to look at the NX window for an open "
        "dialog instead of retrying."
    ),
)

Product = Literal["gen", "civil"]

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
# Leaves the model alone but has NX write a file, so not "read only".
WRITES_FILE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
CHANGES_MODEL = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
# Changes which file NX works on, destroys nothing.
SWITCHES_FILE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
SAVE_PREFIX = "mcp-save-"
SAVE_EXTENSIONS = {"gen": ".mgbx", "civil": ".mcbz"}  # CLAUDE.md rule 5
MAX_WRITE_RECORDS = 50
DIALOG_POLL_SECONDS = 1.0
CAPTURE_PREFIX = "mcp-capture-"
MAX_CAPTURE_PIXELS = 2000

# read_model_info's choices: the SDK's GET-only /ope and /view queries.
MODEL_INFO = {
    "project_status": ope.get_project_status,
    "section_properties": ope.get_section_properties,
    "story_check_parameter": ope.get_story_check_parameter,
    "story_irregularity_parameter": ope.get_story_irregularity_check_parameter,
    "selection": view.get_selection,
}
# Live: these two answer 404 on Civil NX.
GEN_ONLY_INFO = {"story_check_parameter", "story_irregularity_parameter"}
ModelInfo = Literal[
    "project_status",
    "section_properties",
    "story_check_parameter",
    "story_irregularity_parameter",
    "selection",
]

# Counted by model_summary, in this order.
SUMMARY_TABLES = ("NODE", "ELEM", "MATL", "SECT", "THIK", "STLD")


def _client(product: Product, timeout: float | None = None) -> MidasClient:
    return MidasClient(
        mapi_key=config.mapi_key(product),
        product=product,
        timeout=config.timeout() if timeout is None else timeout,
    )


def _open_windows_note(product: Any) -> str:
    """" NX windows open now: ..." when NX runs here and shows any besides its main one."""
    if product not in nx_window.EXECUTABLES:
        return ""
    titles = sorted(nx_window.other_windows(product))
    return f" NX windows open now: {', '.join(repr(t) for t in titles)}." if titles else ""


def _guarded(fn):
    """Turn every failure into a ToolError the model can read.

    The MCP SDK hides the text of any other exception from the client, which
    would swallow the NX server's own message (CLAUDE.md rule 6).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except MidasConnectionError as exc:
            if isinstance(exc.__cause__, requests.Timeout):
                product = kwargs.get("product", args[0] if args else None)
                raise ToolError(
                    f"NX did not answer within {config.timeout():g} s. NX is probably "
                    "showing a dialog that blocks the API. Ask the user to look at the "
                    "NX window and close it; do not retry until they have."
                    + _open_windows_note(product)
                ) from exc
            raise ToolError(str(exc)) from exc
        except (MidasAPIError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _table_class(table: str):
    name = table.strip().strip("/").upper().removeprefix("DB/")
    tables = catalog.db_tables()
    if name not in tables:
        close = difflib.get_close_matches(name, tables, n=5)
        hint = f" Close matches: {', '.join(close)}." if close else ""
        raise ToolError(f"Unknown table {table!r}.{hint} Call list_tables to see all.")
    return tables[name]


def _records(response: Any) -> tuple[dict[str, Any], str]:
    """``({id: record}, server message)`` from a GET /db/<NAME> response.

    An empty table comes back as ``{"<NAME>": {}}`` or as a bare
    ``{"message": ...}``; the message is passed on, never judged here.
    """
    if not isinstance(response, dict):
        return {}, str(response)
    records = next((v for v in response.values() if isinstance(v, dict)), {})
    return records, str(response.get("message", ""))


def _limit(limit: int | None) -> int:
    cap = config.max_rows()
    return cap if limit is None else max(1, min(limit, cap))


@mcp.tool(annotations=READ_ONLY)
@_guarded
def check_connection(product: Product) -> dict:
    """Check whether the given MIDAS NX product is reachable through the Open API.

    Returns the connection status and the program name. A blocked NX session
    (an open dialog) can still report "connected"; a later read then times out.
    """
    health = _client(product).verify_connection()
    # The response also carries the MAPI account's email; leave it out.
    return {"product": product, "status": health.get("status"), "program": health.get("program")}


@mcp.tool(annotations=READ_ONLY)
@_guarded
def list_tables(product: Product | None = None, search: str = "") -> list[dict]:
    """List the tables this server can read: /db model data (e.g. "NODE") and
    /DESIGN design parameters (e.g. "DESIGN/STEEL/DSTL", the steel design code).

    product: keep only tables that product supports. search: case-insensitive
    text matched against the table name and title, e.g. "load", "section",
    "design". No NX call is made.
    """
    needle = search.strip().lower()
    rows = []
    for name, cls in catalog.db_tables().items():
        if product and product not in cls.PRODUCTS:
            continue
        if needle and needle not in name.lower() and needle not in cls.NAME.lower():
            continue
        rows.append({"table": name, "title": cls.NAME, "products": sorted(cls.PRODUCTS)})
    return rows


@mcp.tool(annotations=READ_ONLY)
@_guarded
def describe_table(product: Product, table: str) -> dict:
    """The NX server's own field schema for one /db table (GET /info/db/<TABLE>).

    Call this before read_table to learn what each field means.
    """
    cls = _table_class(table)
    if cls.ENDPOINT.startswith("/DESIGN/"):
        # Asked live on both products: /info/DESIGN/... answers 404.
        raise ToolError(
            "NX publishes no schema for /DESIGN tables. Use read_table; field names "
            "are in the records it returns."
        )
    return cls.info(client=_client(product))


@mcp.tool(annotations=READ_ONLY)
@_guarded
def read_table(
    product: Product, table: str, ids: list[int] | None = None, limit: int | None = None
) -> dict:
    """Records of one table in the open model, e.g. table="NODE" or "DESIGN/STEEL/DSTL".

    ids: return only these record ids. limit: at most this many records
    (capped by the server's MIDAS_MAX_ROWS). The reply says how many records
    the table holds in all, so a truncated answer is never mistaken for the
    whole table.
    """
    cls = _table_class(table)
    records, message = _records(cls.get(client=_client(product)))
    if ids is not None:
        wanted = {str(i) for i in ids}
        selected = {k: v for k, v in records.items() if k in wanted}
        missing = sorted(wanted - selected.keys(), key=lambda k: (len(k), k))
    else:
        selected, missing = records, []
    n = _limit(limit)
    shown = dict(list(selected.items())[:n])
    result: dict[str, Any] = {
        "table": catalog.table_name(cls.ENDPOINT),
        "total_in_table": len(records),
        "returned": len(shown),
        "records": shown,
    }
    if len(selected) > n:
        result["note"] = f"{len(selected) - n} more records not shown; pass ids or a smaller set"
    if missing:
        result["missing_ids"] = missing
    if message:
        result["server_message"] = message
    return result


@mcp.tool(annotations=READ_ONLY)
@_guarded
def model_summary(product: Product) -> dict:
    """Record counts of the main tables (nodes, elements, materials, sections,
    thicknesses, static load cases), plus the model's unit system.

    A table that fails to read is reported with its error, the rest still count.
    """
    client = _client(product)
    tables = catalog.db_tables()
    counts: dict[str, Any] = {}
    for name in SUMMARY_TABLES:
        cls = tables.get(name)
        if cls is None or product not in cls.PRODUCTS:
            continue
        try:
            records, _ = _records(cls.get(client=client))
            counts[name] = len(records)
        except MidasConnectionError:
            raise  # a timeout means NX is blocked; stop here, not five times over
        except MidasAPIError as exc:
            counts[name] = f"error: {exc}"
    summary: dict[str, Any] = {"product": product, "counts": counts}
    unit = tables.get("UNIT")
    if unit is not None:
        try:
            summary["unit"], _ = _records(unit.get(client=client))
        except MidasAPIError as exc:
            summary["unit"] = f"error: {exc}"
    return summary


@mcp.tool(annotations=READ_ONLY)
@_guarded
def read_model_info(product: Product, what: ModelInfo, limit: int | None = None) -> dict:
    """Model-wide information NX computes, not stored in a table.

    what: "project_status" (counts of model and load data), "section_properties"
    (computed area, inertia, ... per section id), "story_check_parameter" and
    "story_irregularity_parameter" (Gen only), or "selection" (what the user
    has selected in the NX window now). Entries keyed by id are capped like
    read_table.
    """
    if what in GEN_ONLY_INFO and product != "gen":
        raise ToolError(f"{what} exists on Gen NX only.")
    response = MODEL_INFO[what](client=_client(product))
    result: dict[str, Any] = {"what": what, "data": response}
    records, _ = _records(response)
    n = _limit(limit)
    if len(records) > n:
        key = next(k for k, v in response.items() if v is records)
        result["data"] = {**response, key: dict(list(records.items())[:n])}
        result["note"] = f"{len(records) - n} more entries not shown"
    return result


@mcp.tool(annotations=READ_ONLY)
@_guarded
def list_result_tables(search: str = "") -> list[dict]:
    """List the result/report table types result_table accepts.

    search: case-insensitive text matched against the type and its name,
    e.g. "reaction", "beam", "story". No NX call is made.
    """
    needle = search.strip().lower()
    return [
        {"table_type": table_type, "name": name}
        for table_type, name in catalog.result_table_types().items()
        if not needle or needle in table_type.lower() or needle in name.lower()
    ]


@mcp.tool(annotations=READ_ONLY)
@_guarded
def result_table(
    product: Product,
    table_type: str,
    load_cases: list[str] | None = None,
    components: list[str] | None = None,
    keys: list[int] | None = None,
    limit: int | None = None,
) -> dict:
    """One result table from the open model (POST /post/TABLE, nothing exported).

    table_type: from list_result_tables, e.g. "REACTIONG". load_cases: names
    with a type suffix, e.g. ["DL(ST)", "COMB1(CB)"]. components: column
    names to keep. keys: node or element ids to restrict to. Analysis result
    tables need the analysis to have been run in NX. Rows beyond limit
    (capped by MIDAS_MAX_ROWS) are counted, not returned.
    """
    table_type = table_type.strip().upper()
    if table_type not in catalog.result_table_types():
        close = difflib.get_close_matches(table_type, catalog.result_table_types(), n=5)
        hint = f" Close matches: {', '.join(close)}." if close else ""
        raise ToolError(
            f"Unknown table_type {table_type!r}.{hint} Call list_result_tables to see all."
        )
    # Never pass export_path (writes a file on the NX machine) or opt_cs (switches
    # NX's construction-stage view): phase 1 reads, it does not change state.
    response = get_table(
        table_type,
        load_case_names=load_cases,
        components=components,
        node_elems={"KEYS": keys} if keys else None,
        client=_client(product),
    )
    table = unwrap_table(response)
    if not table:
        # No table in the body: hand the server's own words back (rule 6).
        message = response.get("message", "") if isinstance(response, dict) else response
        return {"table_type": table_type, "rows": 0, "server_message": str(message) or None}
    data = table.get("DATA") or []
    n = _limit(limit)
    result: dict[str, Any] = {
        "table_type": table_type,
        "units": {k: table[k] for k in ("FORCE", "DIST") if k in table},
        "head": table.get("HEAD", []),
        "total_rows": len(data),
        "returned": min(len(data), n),
        "data": data[:n],
    }
    if len(data) > n:
        result["note"] = f"{len(data) - n} more rows not shown; narrow with keys or load_cases"
    return result


@mcp.tool(annotations=WRITES_FILE)
@_guarded
def capture_view(product: Product, width: int | None = None, height: int | None = None) -> Image:
    """A PNG of the model window as NX shows it now (POST /view/CAPTURE).

    Shows the current view: viewpoint, active elements and result display are
    whatever the user has set in NX; this tool does not change them. width and
    height are in pixels (each at most 2000); leave them out for the window size.
    """
    argument: dict[str, Any] = {}
    for name, value in (("WIDTH", width), ("HEIGHT", height)):
        if value is not None:
            if not 1 <= value <= MAX_CAPTURE_PIXELS:
                raise ToolError(f"{name.lower()} must be 1..{MAX_CAPTURE_PIXELS}, got {value}")
            argument[name] = value
    path = _generated_path(CAPTURE_PREFIX, product, ".png")
    capture({"EXPORT_PATH": path.as_posix(), **argument}, client=_client(product))
    if not _file_landed(path):
        raise ToolError(
            f"NX answered the capture but {path} did not appear on this PC. NX may be "
            "running on another computer, where the file was written instead."
        )
    data = path.read_bytes()
    path.unlink()  # our own generated file only (rule 8)
    return Image(data=data, format="png")


def _generated_path(prefix: str, product: str, suffix: str) -> Path:
    """A new file name in the output folder (CLAUDE.md rule 5); never one we were given."""
    folder = config.output_dir()
    if not folder.is_dir():
        # A missing folder raises a modal dialog in NX and blocks the session.
        raise ToolError(f"Output folder {folder} does not exist on this PC; not asking NX.")
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    return folder / f"{prefix}{product}-{stamp}{suffix}"


def _file_landed(path: Path) -> bool:
    """NX answers once it has accepted the call; give the file a moment to land."""
    for _ in range(20):
        if path.is_file() and path.stat().st_size > 0:
            return True
        time.sleep(0.25)
    return False


# --- Phase 2: changes to the model or its file --------------------------------
#
# Each tool runs twice. Without confirm_token it changes nothing and returns a
# preview plus a token computed from the request and the current state; with
# that token it recomputes it and applies only on a match, so a skipped preview
# or a model changed in between is refused rather than acted on.


def _token(*parts: Any) -> str:
    text = json.dumps(parts, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _check_token(given: str, expected: str) -> None:
    if given != expected:
        raise ToolError(
            "confirm_token does not match: the request or the model changed since the "
            "preview. Preview again (no confirm_token) and show it to the user."
        )


def _small(value: Any, limit: int = 2000) -> Any:
    """The server's response as is, unless it is too long to be worth sending."""
    text = json.dumps(value, ensure_ascii=True, default=str)
    return value if len(text) <= limit else f"{text[:limit]}... ({len(text)} chars)"


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed = {
        k: {"before": before[k], "after": after[k]}
        for k in before.keys() & after.keys()
        if before[k] != after[k]
    }
    by_id = lambda k: (len(k), k)
    return {
        "added": {k: after[k] for k in sorted(after.keys() - before.keys(), key=by_id)},
        "changed": dict(sorted(changed.items(), key=lambda kv: by_id(kv[0]))),
        "removed": sorted(before.keys() - after.keys(), key=by_id),
    }


def _writable(table: str, method: str):
    cls = _table_class(table)
    if method not in cls.METHODS:
        raise ToolError(
            f"{catalog.table_name(cls.ENDPOINT)} does not accept {method}; "
            f"it supports {sorted(cls.METHODS)}."
        )
    return cls


# Measured (SDK live notes): any write discards the analysis results, even one
# that stores the value already there.
RESULTS_WARNING = (
    "Applying this discards the analysis results in NX; run_analysis again before "
    "reading result tables."
)
# Measured on the /DESIGN tables (SDK live notes, 2026-09-28/29).
DESIGN_NOTE = (
    "/DESIGN tables: DCTL and LENG are one record shared by the RC, steel and SRC "
    "codes; RC/steel SRDF and LMRR are refused until that code's DCO is written; "
    "Civil refuses the RC and steel design code selection; a DELETE may reset a "
    "record to defaults instead of removing it."
)


def _preview(cls, **fields: Any) -> dict[str, Any]:
    preview: dict[str, Any] = {"preview": True, **fields, "warning": RESULTS_WARNING}
    if cls.ENDPOINT.startswith("/DESIGN/"):
        preview["design_note"] = DESIGN_NOTE
    return preview


def _record_ids(ids: Any) -> list[str]:
    keys = [str(i).strip() for i in ids]
    if not keys:
        raise ToolError("No record ids given.")
    if len(keys) > MAX_WRITE_RECORDS:
        raise ToolError(f"At most {MAX_WRITE_RECORDS} records per call, got {len(keys)}.")
    if any(not k.isdigit() or int(k) < 1 for k in keys) or len(set(keys)) != len(keys):
        raise ToolError(f"Record ids must be distinct positive integers, got {keys}.")
    return keys


@mcp.tool(annotations=CHANGES_MODEL)
@_guarded
def run_analysis(product: Product, confirm_token: str | None = None) -> dict:
    """Run the analysis in NX (POST /doc/ANAL). Two steps: preview, then apply.

    NX saves the open document over its file before analysing (leaving a .bak),
    unsaved changes included, even when it then refuses the analysis. So this
    needs a document that has been saved before, seen in the NX window title on
    this PC; a new, never-saved document would raise a Save As dialog that
    blocks NX, and is refused here.
    """
    document = nx_window.open_document(product)
    if document is None:
        raise ToolError(
            "Cannot see the NX window on this PC, so cannot confirm the document is "
            "saved. Run the analysis in NX itself (F5)."
        )
    if document.path is None:
        raise ToolError(
            "The open document has never been saved; analysing it would raise a Save "
            "As dialog that blocks NX. Ask the user to save it in NX first."
        )
    token = _token("analysis", product, document.path, document.unsaved_changes)
    if confirm_token is None:
        warning = f"NX will save the document over {document.path} (keeping a .bak) first"
        if document.unsaved_changes:
            warning += ", including changes not yet saved"
        return {"preview": True, "file": document.path, "warning": warning + ".",
                "confirm_token": token}
    _check_token(confirm_token, token)
    timeout = config.analysis_timeout()
    client = _client(product, timeout=timeout)
    # NX may stop before solving to ask something (e.g. "Check Changed Story
    # Information"), and the call then waits on the user. Watch for a window
    # that was not there before, and say so at once instead of going quiet.
    windows_before = nx_window.other_windows(product)
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(doc.analyze, client=client)
    pool.shutdown(wait=False)
    while True:
        try:
            response = future.result(timeout=DIALOG_POLL_SECONDS)
            break
        except FutureTimeout:
            dialogs = sorted(nx_window.other_windows(product) - windows_before)
            if dialogs:
                future.add_done_callback(_log_late_analysis)
                return {
                    "status": "waiting_for_nx_dialog",
                    "nx_dialog": dialogs,
                    "seconds": round(time.monotonic() - started, 1),
                    "message": (
                        "The analysis started, and NX is now asking the user something in "
                        "the window(s) named above. Ask the user to answer it in NX. Do not "
                        "start the analysis again: it continues once the dialog is answered. "
                        "Read results with result_table afterwards."
                    ),
                }
        except MidasConnectionError as exc:
            if isinstance(exc.__cause__, requests.Timeout):
                raise ToolError(
                    f"No answer within {timeout:g} s. The analysis may still be running, "
                    "or NX may show a dialog. Ask the user to look at the NX window; do "
                    "not start the analysis again." + _open_windows_note(product)
                ) from exc
            raise
    return {
        "status": "done",
        "server_message": response.get("message") if isinstance(response, dict) else response,
        "seconds": round(time.monotonic() - started, 1),
    }


def _log_late_analysis(future) -> None:
    """How an analysis we stopped waiting for ended; stderr only, for the log."""
    error = future.exception()
    log.info("analysis finished after a dialog: %s", error or future.result())


@mcp.tool(annotations=SWITCHES_FILE)
@_guarded
def save_copy(product: Product, confirm_token: str | None = None) -> dict:
    """Save the model as a new file in the output folder (POST /doc/SAVEAS).
    Two steps: preview, then apply.

    Like Save As in NX: from then on NX works on the copy, so the window, later
    saves and run_analysis use it, and the original file stays as it was last
    saved. The name is generated (mcp-save-<product>-<time>, .mgbx for Gen,
    .mcbz for Civil). Also the way to give a never-saved document a file, which
    run_analysis needs.
    """
    document = nx_window.open_document(product)
    if document is None:
        raise ToolError(
            "Cannot see the NX window on this PC, so cannot check that the copy is "
            "written. Use Save As in NX itself."
        )
    token = _token("save_copy", product, document.path, document.unsaved_changes)
    if confirm_token is None:
        warning = (
            f"NX will save the model as a new file in {config.output_dir()} and go on "
            "working on that copy. "
        )
        if document.path is None:
            warning += "The open document has never been saved; the copy becomes its file."
        else:
            warning += f"{document.path} stays as it was last saved" + (
                ", without the changes not yet saved." if document.unsaved_changes else "."
            )
        return {"preview": True, "folder": str(config.output_dir()),
                "original": document.path, "warning": warning, "confirm_token": token}
    _check_token(confirm_token, token)
    path = _generated_path(SAVE_PREFIX, product, SAVE_EXTENSIONS[product])
    response = doc.save_as(path.as_posix(), client=_client(product))
    message = response.get("message") if isinstance(response, dict) else response
    # Civil answers "command complete" for a save it did not write (SDK live
    # notes); the disk and the window title are what count.
    if not _file_landed(path):
        raise ToolError(f"NX answered {message!r}, but {path} is not on disk: nothing was saved.")
    now = nx_window.open_document(product)
    result: dict[str, Any] = {"applied": True, "file": str(path), "server_message": message,
                              "now_open": now.path if now else None}
    if now is None or now.path is None or (
        os.path.normcase(now.path) != os.path.normcase(str(path.with_suffix("")))
    ):
        result["warning"] = (
            "The copy is on disk, but the NX window does not show it as the open document."
        )
    return result


@mcp.tool(annotations=CHANGES_MODEL)
@_guarded
def write_records(
    product: Product,
    table: str,
    records: dict[str, dict[str, Any]],
    mode: Literal["create", "update"],
    confirm_token: str | None = None,
) -> dict:
    """Create or update records in one /db or /DESIGN table. Two steps: preview, then apply.

    records: {id: fields}, e.g. {"13": {"X": 0, "Y": 0, "Z": 6.4}} for NODE; see
    describe_table for the fields (or read_table, for /DESIGN). "create"
    refuses ids that exist, "update" ids that do not, except on a table with
    no POST (most /DESIGN tables: one record, "update" only), where "update"
    also creates it. At most 50 records. After applying, the
    table is read back and what actually changed is returned; some tables store
    a new record under an id of their own choosing, so trust "added", not the
    ids you sent. Any write discards the analysis results.
    """
    method = "POST" if mode == "create" else "PUT"
    cls = _writable(table, method)
    name = catalog.table_name(cls.ENDPOINT)
    ids = _record_ids(records)
    payload = {k.strip(): v for k, v in records.items()}
    client = _client(product)
    before, _ = _records(cls.get(client=client))
    if mode == "create":
        clash = [i for i in ids if i in before]
        if clash:
            raise ToolError(f"{name} already has ids {clash}; use mode='update' for those.")
    elif "POST" in cls.METHODS:
        missing = [i for i in ids if i not in before]
        if missing:
            raise ToolError(f"{name} has no ids {missing}; use mode='create' for those.")
    # Otherwise a PUT-only table (most /DESIGN singletons): its PUT is also how
    # the record comes to exist on a fresh document (SDK live notes).
    current = {i: before[i] for i in ids if i in before}
    token = _token("write", product, name, mode, payload, current, len(before))
    if confirm_token is None:
        return _preview(cls, table=name, mode=mode, current=current, will_send=payload,
                        confirm_token=token)
    _check_token(confirm_token, token)
    items = {int(k): v for k, v in payload.items()}
    response = (cls.create if mode == "create" else cls.update)(items, client=client)
    after, _ = _records(cls.get(client=client))
    result: dict[str, Any] = {"applied": True, "table": name, **_diff(before, after),
                              "server_response": _small(response)}
    if not (result["added"] or result["changed"] or result["removed"]):
        result["warning"] = "NX answered, but reading the table back shows no change."
    elif mode == "create" and not set(ids) <= result["added"].keys():
        result["warning"] = "NX stored the new records under ids other than the ones sent."
    return result


@mcp.tool(annotations=CHANGES_MODEL)
@_guarded
def delete_records(
    product: Product, table: str, ids: list[int], confirm_token: str | None = None
) -> dict:
    """Delete records by id from one /db or /DESIGN table. Two steps: preview, then apply.

    Only the listed ids, one DELETE per id; there is no way to empty a table
    here. Nodes still used by an element are refused (NX would delete the
    elements with them). At most 50 ids. The table is read back afterwards: on
    some /DESIGN tables NX resets the record to defaults rather than removing
    it, or keeps it unchanged, and the result says which.
    """
    cls = _writable(table, "DELETE")
    name = catalog.table_name(cls.ENDPOINT)
    keys = _record_ids(ids)
    client = _client(product)
    tables = catalog.db_tables()
    before, _ = _records(cls.get(client=client))
    present = [k for k in keys if k in before]
    if not present:
        raise ToolError(f"None of ids {keys} exist in {name}; nothing to delete.")
    if name == "NODE":
        elements, _ = _records(tables["ELEM"].get(client=client))
        wanted = {int(k) for k in present}
        users = {e: sorted(wanted & set(rec.get("NODE", []))) for e, rec in elements.items()}
        users = {e: n for e, n in users.items() if n}
        if users:
            raise ToolError(
                f"Nodes in use by elements (element: nodes) {users}; NX would delete those "
                "elements too. Delete the elements first if that is intended."
            )
    # Deleting one record can take others with it; count the main tables around it.
    watched = [t for t in ("NODE", "ELEM") if t != name]
    counts = {t: len(_records(tables[t].get(client=client))[0]) for t in watched}
    token = _token("delete", product, name, present, {k: before[k] for k in present}, counts)
    if confirm_token is None:
        return _preview(cls, table=name, will_delete={k: before[k] for k in present},
                        not_found=[k for k in keys if k not in before], confirm_token=token)
    _check_token(confirm_token, token)
    responses = cls.delete([int(k) for k in present], client=client)
    after, _ = _records(cls.get(client=client))
    result: dict[str, Any] = {"applied": True, "table": name, **_diff(before, after),
                              "server_response": _small(responses)}
    gone = set(result["removed"])
    if gone != set(present):
        kept = sorted(set(present) - gone, key=lambda k: (len(k), k))
        reset = [k for k in kept if k in result["changed"]]
        unchanged = [k for k in kept if k not in result["changed"]]
        notes = [f"ids {reset} are still there, reset by NX (see changed)"] if reset else []
        if unchanged:
            notes.append(f"ids {unchanged} are still there, unchanged: NX did not delete them")
        result["warning"] = "; ".join(notes) + "."
    side = {t: (n, len(_records(tables[t].get(client=client))[0])) for t, n in counts.items()}
    side = {t: {"before": b, "after": a} for t, (b, a) in side.items() if b != a}
    if side:
        result["also_changed"] = side
    return result


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()

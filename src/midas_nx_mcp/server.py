"""The MCP server: read tools (phase 1) and previewed writes (phase 2, see PLAN.md).

Never print(): stdout carries the MCP protocol. Log to stderr.
"""

from __future__ import annotations

import difflib
import functools
import hashlib
import inspect
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
from midas_nx import MidasAPIError, MidasClient, MidasConnectionError, doc, ope, plan, view
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
        "describe_table before read_table; list_result_tables before result_table "
        "(it says what each category of table needs in NX first). "
        "capture_view shows the NX model window as an image; show_result first sets "
        "a result graphic (reactions, displacement, deformed shape, beam diagram, "
        "plate forces) and then captures it. "
        "run_analysis, save_copy, write_records, delete_records and "
        "generate_load_combinations change the model or its file: call them first "
        "without confirm_token, show the preview to the user, "
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
SAVE_EXTENSIONS = {"gen": ".mgbx", "civil": ".mcbz"}  # AGENTS.md rule 5
SAVE_LANDING_SECONDS = 30.0  # a big model is still being written after NX answers
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

# list_result_tables' categories, by the midas_nx.post module that defines the type.
RESULT_CATEGORIES = {
    "pre_process": ("model", "Model data; no analysis needed."),
    "result_1": ("analysis", "Needs the analysis run in NX (run_analysis)."),
    "story": (
        "story",
        (
            "Gen only. Needs stories defined in NX (its Story command, with the story "
            "data calculated) before the analysis is run."
        ),
    ),
    "design": ("design", "Needs the analysis and the design settings (code, members) in NX."),
}
ResultCategory = Literal["model", "analysis", "story", "design"]
NO_RESULT_TEXT = "no analysis result"

# show_result: NX's result mode and its components, the default first. Live on
# Gen NX (2026-11-04) NX took and drew every pair here; it refuses a name it
# does not know with "second query is wrong". Civil NX not yet tried.
RESULT_VIEWS = {
    "reactions": ("reactionforces/moments", ("FZ", "FX", "FY", "MX", "MY", "MZ")),
    "displacement": ("displacementcontour", ("DXYZ", "DX", "DY", "DZ")),
    "deformed_shape": ("deformedshape", ("DXYZ", "DX", "DY", "DZ")),
    "beam_diagram": ("beamdiagrams", ("My", "Mz", "Fx", "Fy", "Fz", "Mx")),
    "plate_forces": ("plateforces/moments", ("Mxx", "Myy", "Mxy", "Fxx", "Fyy", "Fxy")),
}
ResultView = Literal["reactions", "displacement", "deformed_shape", "beam_diagram", "plate_forces"]

# generate_load_combinations: the SDK call, its design code, the table it fills.
# Live on Gen (SDK, 2026-10-26): each adds two or more; Civil answers 404.
COMBINATION_KINDS = {
    "concrete": (ope.generate_load_combination_concrete, "KDS 41 20 : 2022", "LCOM-CONC"),
    "steel": (ope.generate_load_combination_steel, "KDS 41 30 : 2022", "LCOM-STEEL"),
    "src": (ope.generate_load_combination_src, "KDS 41 SRC : 2022", "LCOM-SRC"),
}
CombinationKind = Literal["concrete", "steel", "src"]
WRONG_QUERY_TEXT = "second query is wrong"

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


def _is_running(product: Any) -> bool | None:
    """nx_window.is_running, or None when it cannot tell (or fails: this runs
    while another error is being reported, and must not replace it)."""
    if product not in nx_window.EXECUTABLES:
        return None
    try:
        return nx_window.is_running(product)
    except Exception:
        log.exception("could not list the NX windows")
        return None


def _not_running_note(product: Any) -> str:
    """A hint when the product has no window here: closed, or running on another PC."""
    if _is_running(product) is not False:
        return ""
    return (
        f" No {product.capitalize()} NX window is open on this PC: NX is not running "
        "(start it and connect the API under Apps > API Settings), or it runs on another PC."
    )


# Live, Gen NX closed: a read answers "404: client does not exist", which
# reads like a wrong id. Only this answer gets the note; NX's other errors
# are passed on as they are (AGENTS.md rule 6).
NOT_CONNECTED_TEXT = "client does not exist"


def _open_project(product: Product, cannot_see: str) -> nx_window.Document:
    """The document NX has open, or a ToolError saying why there is none to act on.

    Every tool that needs the open document goes through here, so the start
    page (no project) is refused in one place.
    """
    document = nx_window.open_document(product)
    if document is None:
        raise ToolError(_not_running_note(product).strip() or cannot_see)
    if not document.project_open:
        raise ToolError(
            "NX shows its start page: no project is open. Ask the user to open or "
            "create one in NX."
        )
    return document


def _product_of(signature: inspect.Signature, args: tuple, kwargs: dict) -> Any:
    try:
        return signature.bind(*args, **kwargs).arguments.get("product")
    except TypeError:
        return None


def _guarded(fn):
    """Turn every failure into a ToolError the model can read.

    The MCP SDK hides the text of any other exception from the client, which
    would swallow the NX server's own message (AGENTS.md rule 6).
    """

    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except MidasConnectionError as exc:
            product = _product_of(signature, args, kwargs)
            if isinstance(exc.__cause__, requests.Timeout):
                raise ToolError(
                    f"NX did not answer within {config.timeout():g} s. NX is probably "
                    "showing a dialog that blocks the API. Ask the user to look at the "
                    "NX window and close it; do not retry until they have."
                    + _open_windows_note(product)
                ) from exc
            raise ToolError(str(exc) + _not_running_note(product)) from exc
        except MidasAPIError as exc:
            note = ""
            if NOT_CONNECTED_TEXT in str(exc):
                note = _not_running_note(_product_of(signature, args, kwargs))
            raise ToolError(str(exc) + note) from exc
        except ValueError as exc:
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
    result = {"product": product, "status": health.get("status"), "program": health.get("program")}
    if result["status"] != "connected":
        running = _is_running(product)
        if running is False:
            result["hint"] = _not_running_note(product).strip()
        elif running:
            result["hint"] = "NX is running, but its API is not connected: Apps > API Settings in NX."
        else:
            result["hint"] = (
                "NX is closed, or its API is not connected (Apps > API Settings in NX); "
                "this server cannot see NX windows to tell which."
            )
    return result


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
        missing = sorted(wanted - selected.keys(), key=_by_id)
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
def list_result_tables(search: str = "", category: ResultCategory | None = None) -> dict:
    """List the result/report table types result_table accepts, by category.

    Categories: "model" (mass, weights, loads, sections, supports; no analysis
    needed), "analysis" (reactions, displacements, member forces, stresses,
    modes), "story" (Gen: story drift, displacement, shear, stability,
    irregularity checks), "design" (design member forces). Each category says
    what NX needs before its tables have data.

    search: case-insensitive text matched against the type and its name,
    e.g. "reaction", "beam", "drift". No NX call is made.
    """
    needle = search.strip().lower()
    tables = [
        {"table_type": table_type, "name": name, "category": RESULT_CATEGORIES[group][0]}
        for table_type, name in catalog.result_table_types().items()
        for group in [name.split(".", 1)[0]]
        if (category is None or RESULT_CATEGORIES[group][0] == category)
        and (not needle or needle in table_type.lower() or needle in name.lower())
    ]
    shown = {t["category"] for t in tables}
    return {
        "categories": {c: needs for c, needs in RESULT_CATEGORIES.values() if c in shown},
        "tables": tables,
    }


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

    table_type: from list_result_tables. Common ones: "REACTIONG" (support
    reactions), "DISPLACEMENTG" (nodal displacements), "BEAMFORCE" (beam/column
    member forces), "PLATEFORCEL" (plate forces), "EIGENVALUEMODE" (periods and
    modes), "STORY_MASS" / "STORYWEIGHT" (story mass and weight, no analysis
    needed), "STORY_DRIFT_X" / "STORY_DRIFT_Y" and "STORY_SHEAR_FOR_RS" (Gen
    story checks), "BEAMDESIGNFORCES" / "COLUMNDESIGNFORCES" (design forces).
    load_cases: names with a type suffix, e.g. ["DL(ST)", "COMB1(CB)"]; the
    model's load cases are in read_table("STLD"). components: column names to
    keep. keys: node or element ids to restrict to. Rows beyond limit (capped
    by MIDAS_MAX_ROWS) are counted, not returned.
    """
    table_type = table_type.strip().upper()
    if table_type not in catalog.result_table_types():
        close = difflib.get_close_matches(table_type, catalog.result_table_types(), n=5)
        hint = f" Close matches: {', '.join(close)}." if close else ""
        raise ToolError(
            f"Unknown table_type {table_type!r}.{hint} Call list_result_tables to see all."
        )
    group = catalog.result_table_types()[table_type].split(".", 1)[0]
    # Never pass export_path (writes a file on the NX machine) or opt_cs (switches
    # NX's construction-stage view): phase 1 reads, it does not change state.
    try:
        response = get_table(
            table_type,
            load_case_names=load_cases,
            components=components,
            node_elems={"KEYS": keys} if keys else None,
            client=_client(product),
        )
    except MidasAPIError as exc:
        if group != "story" or NO_RESULT_TEXT not in str(exc):
            raise
        # Live: an analysed model still answers this for story tables unless
        # the stories were calculated before the analysis.
        raise ToolError(f"{exc} {RESULT_CATEGORIES['story'][1]}") from exc
    table = unwrap_table(response)
    if not table:
        # No table in the body: hand the server's own words back (rule 6).
        message = _server_message(response)
        return {"table_type": table_type, "rows": 0, "server_message": str(message or "") or None}
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
    return _capture(product, _capture_size(width, height))


def _capture_size(width: int | None, height: int | None) -> dict[str, int]:
    size = {}
    for name, value in (("WIDTH", width), ("HEIGHT", height)):
        if value is not None:
            if not 1 <= value <= MAX_CAPTURE_PIXELS:
                raise ToolError(f"{name.lower()} must be 1..{MAX_CAPTURE_PIXELS}, got {value}")
            size[name] = value
    return size


def _capture(product: Product, size: dict[str, int]) -> Image:
    path = _generated_path(CAPTURE_PREFIX, product, ".png")
    capture({"EXPORT_PATH": path.as_posix(), **size}, client=_client(product))
    if not _file_landed(path):
        raise ToolError(
            f"NX answered the capture but {path} did not appear on this PC. NX may be "
            "running on another computer, where the file was written instead."
        )
    data = path.read_bytes()
    path.unlink()  # our own generated file only (rule 8)
    return Image(data=data, format="png")


@mcp.tool(annotations=WRITES_FILE)
@_guarded
def show_result(
    product: Product,
    result: ResultView,
    load_case: str,
    component: str | None = None,
    show_values: bool = False,
    deform_scale: float | None = None,
    width: int | None = None,
    height: int | None = None,
) -> Image:
    """Show an analysis result in the NX window and return it as a PNG.

    Like picking a result under Results in NX (POST /view/RESULTGRAPHIC, then
    /view/CAPTURE): the window keeps showing it afterwards. The model and its
    results are not changed. Needs the analysis run in NX.

    result and its components (default first):
      "reactions"      FZ, FX, FY, MX, MY, MZ (values are always shown)
      "displacement"   DXYZ, DX, DY, DZ (contour)
      "deformed_shape" DXYZ, DX, DY, DZ (deformed over undeformed)
      "beam_diagram"   My, Mz, Fx (axial), Fy, Fz (shear), Mx (torsion)
      "plate_forces"   Mxx, Myy, Mxy, Fxx, Fyy, Fxy
    load_case: a name from read_table("STLD"), e.g. "DL"; a combination with
    its type suffix as result_table takes it, e.g. "gLCB1(CB)", "cLCB1(CBC)".
    show_values writes the numbers on the picture; deform_scale scales the
    deformed shape (NX's own scale when left out). width/height: pixels, at
    most 2000 each.
    """
    mode, components = RESULT_VIEWS[result]
    comp = _component(result, component, components)
    case_name, case_type = _load_case(load_case)
    display: dict[str, Any] = {"LEGEND": {"OPT_CHECK": True}}
    if result == "deformed_shape":
        deform: dict[str, Any] = {"OPT_CHECK": True}
        if deform_scale is not None:
            if not deform_scale > 0:
                raise ToolError(f"deform_scale must be above 0, got {deform_scale}")
            deform["SCALE_FACTOR"] = deform_scale
        display |= {"DEFORM": deform, "UNDEFORMED": {"OPT_CHECK": True}}
    elif result != "reactions":
        display["CONTOUR"] = {"OPT_CHECK": True}
    if show_values or result == "reactions":
        display["VALUES"] = {"OPT_CHECK": True}
    size = _capture_size(width, height)
    argument = {
        "CURRENT_MODE": mode,
        "LOAD_CASE_COMB": {"TYPE": case_type, "NAME": case_name},
        "COMPONENTS": {"COMP": comp},
        "TYPE_OF_DISPLAY": display,
    }
    _require_results(product, case_name, case_type)
    try:
        view.set_result_graphic(argument, client=_client(product))
    except MidasAPIError as exc:
        if WRONG_QUERY_TEXT in str(exc):
            # Live: NX's answer to a component or mode it does not take.
            raise ToolError(
                f"{exc} NX did not accept component {comp!r} for {result!r}; "
                f"known ones: {', '.join(components)}."
            ) from exc
        raise
    return _capture(product, size)


def _require_results(product: Product, case_name: str, case_type: str) -> None:
    """Refuse when the model has no analysis results to draw.

    Live (Gen, 2026-11-04): with the results gone (the model was changed after
    the analysis), /view/RESULTGRAPHIC still answers "command complete" and the
    capture is the bare model, which reads like a result picture. A reaction
    table for the same case is the cheapest way to ask; only NX's "no analysis
    result" stops here, anything else is left to /view/RESULTGRAPHIC to report.
    """
    try:
        get_table(
            "REACTIONG", load_case_names=[f"{case_name}({case_type})"], client=_client(product)
        )
    except MidasAPIError as exc:
        if NO_RESULT_TEXT in str(exc):
            raise ToolError(
                f"{exc} Nothing to draw: run the analysis first (run_analysis, or in NX). "
                "Changing the model after an analysis clears its results."
            ) from exc
        log.debug("result check before show_result: %s", exc)


def _component(result: str, component: str | None, known: tuple[str, ...]) -> str:
    if component is None:
        return known[0]
    wanted = component.strip()
    # NX's spelling (My, Fxx, DXYZ) whatever case was asked for; others go to NX as given.
    return next((k for k in known if k.lower() == wanted.lower()), wanted)


def _load_case(load_case: str) -> tuple[str, str]:
    """``"DL"`` -> ``("DL", "ST")``; ``"gLCB1(CB)"`` -> ``("gLCB1", "CB")``."""
    text = load_case.strip()
    if text.endswith(")") and "(" in text:
        name, _, kind = text[:-1].rpartition("(")
        if name.strip() and kind.strip():
            return name.strip(), kind.strip().upper()
    if not text:
        raise ToolError("load_case is empty; read_table('STLD') lists the load cases")
    return text, "ST"


def _output_folder() -> Path:
    folder = config.output_dir()
    if not folder.is_dir():
        # A missing folder raises a modal dialog in NX and blocks the session.
        raise ToolError(f"Output folder {folder} does not exist on this PC; not asking NX.")
    return folder


def _generated_path(prefix: str, product: str, suffix: str) -> Path:
    """A new file name in the output folder (AGENTS.md rule 5); never one we were given."""
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    return _output_folder() / f"{prefix}{product}-{stamp}{suffix}"


def _file_landed(path: Path, seconds: float = 5.0) -> bool:
    """NX answers once it has accepted the call; give the file a moment to land."""
    for _ in range(max(1, int(seconds / 0.25))):
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
    return {
        "added": {k: after[k] for k in sorted(after.keys() - before.keys(), key=_by_id)},
        "changed": dict(sorted(changed.items(), key=lambda kv: _by_id(kv[0]))),
        "removed": sorted(before.keys() - after.keys(), key=_by_id),
    }


def _puts_create(cls, ids: list[str]) -> bool:
    """Whether an update may create these records: only on the /DESIGN singletons
    the SDK measured taking a PUT on a fresh document, and only their record 1."""
    endpoint = cls.ENDPOINT
    return (
        endpoint.startswith("/DESIGN/")
        and endpoint.rsplit("/", 1)[1] in PUT_CREATES_SINGLETONS
        and ids == ["1"]
    )


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
# /DESIGN tables holding one record that a PUT creates on a fresh document
# (SDK live notes, 2026-09-28/29: written by PUT, no POST). Listed by hand on
# purpose: a new GET/PUT-only table in the SDK must not inherit this.
PUT_CREATES_SINGLETONS = frozenset({"DCO", "DCTL", "LLRF", "LMRR", "SRDF"})


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


# The property table an element's SECT names, by element TYPE (midas-nx
# contracts, measured on Gen and Civil 2026-11-04): a /db/SECT section for line
# elements, a /db/THIK thickness for plates and walls. NX checks neither, nor
# MATL: an element naming a record that does not exist is stored as an orphan,
# and deleting a record in use leaves its elements pointing at nothing. Other
# element types (solids, ...) are not checked here.
SECTION_TABLES = {"BEAM": "SECT", "TRUSS": "SECT", "TENSTR": "SECT", "COMPTR": "SECT",
                  "PLATE": "THIK", "WALL": "THIK"}
PROPERTY_TABLES = ("MATL", "SECT", "THIK")
# What NX deletes along with a record, measured (midas-nx contracts, 2026-11-04).
# Nodes in use are refused outright, so only load cases are listed.
CASCADE_NOTES = {
    "STLD": "NX deletes the loads in these load cases along with them (beam, nodal, "
            "pressure loads, ...); also_changed shows the counts after applying.",
}


def _as_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _property_refs(element: dict[str, Any]) -> dict[str, int]:
    """{table: id} of the material and section/thickness one element names."""
    refs: dict[str, int] = {}
    if (matl := _as_id(element.get("MATL"))) is not None:
        refs["MATL"] = matl
    table = SECTION_TABLES.get(str(element.get("TYPE", "")).upper())
    if table and (sect := _as_id(element.get("SECT"))) is not None:
        refs[table] = sect
    return refs


def _check_element_properties(
    client: MidasClient, records: dict[str, Any], before: dict[str, Any]
) -> None:
    """Refuse elements naming a MATL, SECT or THIK that does not exist."""
    named = {k: _property_refs({**before.get(k, {}), **v}) for k, v in records.items()}
    tables = catalog.db_tables()
    missing: dict[str, dict[str, int]] = {}
    for table in PROPERTY_TABLES:
        wanted = {k: refs[table] for k, refs in named.items() if table in refs}
        if not wanted:
            continue
        existing = {_as_id(i) for i in _records(tables[table].get(client=client))[0]}
        for element, ref in wanted.items():
            if ref not in existing:
                missing.setdefault(element, {})[table] = ref
    if missing:
        raise ToolError(
            f"Elements name property records that do not exist (element: table id) {missing}. "
            "NX would store them anyway, pointing at nothing. Create those MATL/SECT/THIK "
            "records first, or use ids that exist."
        )


def _users_of(client: MidasClient, table: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Of the MATL/SECT/THIK ids, those some element names: {id: count and first few}."""
    wanted = {int(k) for k in ids}
    elements, _ = _records(catalog.db_tables()["ELEM"].get(client=client))
    users: dict[int, list[str]] = {}
    for element, rec in elements.items():
        ref = _property_refs(rec).get(table)
        if ref in wanted:
            users.setdefault(ref, []).append(element)
    return {str(i): {"elements": len(e), "first": sorted(e, key=_by_id)[:5]}
            for i, e in sorted(users.items())}


def _watched_tables(name: str) -> list[str]:
    """NODE, ELEM and every table whose records name `name` (midas_nx.plan)."""
    endpoint = f"/db/{name}"
    dependents = [t.removeprefix("/db/") for t, deps in plan.DEPENDENCIES.items()
                  if endpoint in deps]
    tables = catalog.db_tables()
    return [t for t in dict.fromkeys(["NODE", "ELEM", *sorted(dependents)])
            if t != name and t in tables]


def _counts(client: MidasClient, names: list[str]) -> dict[str, int]:
    """Record counts; a table this product or model does not answer is left out."""
    tables = catalog.db_tables()
    counts: dict[str, int] = {}
    for name in names:
        try:
            counts[name] = len(_records(tables[name].get(client=client))[0])
        except MidasAPIError as exc:
            log.debug("not counting %s: %s", name, exc)
    return counts


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
    document = _open_project(
        product,
        "Cannot see the NX window on this PC, so cannot confirm the document is saved. "
        "Run the analysis in NX itself (F5).",
    )
    if document.path is None:
        raise ToolError(
            "The open document has never been saved; analysing it would raise a Save "
            "As dialog that blocks NX. Give it a file first: save_copy (with the user's "
            "OK), or the user saves it in NX."
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
    started = time.monotonic()
    response, dialogs = _call_watching_dialogs(
        product, "analysis", timeout, lambda: doc.analyze(client=_client(product, timeout))
    )
    if dialogs:
        return {
            "status": "waiting_for_nx_dialog",
            "nx_dialog": dialogs,
            "seconds": round(time.monotonic() - started, 1),
            "message": (
                "The analysis started, and NX is now asking the user something in the "
                "window(s) named above. Ask the user to answer it in NX. Do not start the "
                "analysis again: it continues once the dialog is answered. Read results "
                "with result_table afterwards."
            ),
        }
    return {
        "status": "done",
        "server_message": _server_message(response),
        "seconds": round(time.monotonic() - started, 1),
    }


def _call_watching_dialogs(product: Product, what: str, timeout: float, call):
    """Run a long NX call; ``(response, [])``, or ``(None, titles)`` as soon as NX
    opens a window that was not there before (a question to the user, e.g.
    "Check Changed Story Information"), instead of waiting on it in silence.

    The call goes on in the background and finishes once the user answers.
    """
    windows_before = nx_window.other_windows(product)
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(call)
    pool.shutdown(wait=False)
    while True:
        try:
            return future.result(timeout=DIALOG_POLL_SECONDS), []
        except FutureTimeout:
            dialogs = sorted(nx_window.other_windows(product) - windows_before)
            if dialogs:
                future.add_done_callback(functools.partial(_log_late_call, what))
                return None, dialogs
        except MidasConnectionError as exc:
            if isinstance(exc.__cause__, requests.Timeout):
                raise ToolError(
                    f"No answer within {timeout:g} s. The {what} may still be running, or "
                    f"NX may show a dialog. Ask the user to look at the NX window; do not "
                    f"start the {what} again." + _open_windows_note(product)
                ) from exc
            raise


def _log_late_call(what: str, future) -> None:
    """How a call we stopped waiting for ended; stderr only, for the log."""
    error = future.exception()
    log.info("%s finished after a dialog: %s", what, error or future.result())


def _server_message(response: Any) -> Any:
    """NX's own words from a response (AGENTS.md rule 6), passed on as they are."""
    return response.get("message") if isinstance(response, dict) else response


def _by_id(key: str) -> tuple[int, str]:
    """Sort record ids as numbers: "2" before "10"."""
    return len(key), key


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
    document = _open_project(
        product,
        "Cannot see the NX window on this PC, so cannot check that the copy is written. "
        "Use Save As in NX itself.",
    )
    folder = _output_folder()
    token = _token("save_copy", product, document.path, document.unsaved_changes, str(folder))
    if confirm_token is None:
        warning = f"NX will save the model as a new file in {folder} and go on working on that copy. "
        if document.path is None:
            warning += "The open document has never been saved; the copy becomes its file."
        else:
            warning += f"{document.path} stays as it was last saved" + (
                ", without the changes not yet saved." if document.unsaved_changes else "."
            )
        return {"preview": True, "folder": str(folder), "original": document.path,
                "warning": warning, "confirm_token": token}
    _check_token(confirm_token, token)
    path = _generated_path(SAVE_PREFIX, product, SAVE_EXTENSIONS[product])
    # A big model takes a while to write, and a refused path raises a modal
    # in NX (Gen) while the call hangs: the same watch as run_analysis.
    timeout = config.save_timeout()
    response, dialogs = _call_watching_dialogs(
        product, "save", timeout,
        lambda: doc.save_as(path.as_posix(), client=_client(product, timeout)),
    )
    if dialogs:
        return {
            "status": "waiting_for_nx_dialog",
            "nx_dialog": dialogs,
            "file": str(path),
            "message": (
                "NX is asking the user something in the window(s) named above while saving. "
                "Ask the user to answer it in NX, then check the window title for which "
                "file is open. Do not save again until they have."
            ),
        }
    message = _server_message(response)
    # Civil answers "command complete" for a save it did not write (SDK live
    # notes); the disk and the window title are what count.
    if not _file_landed(path, seconds=SAVE_LANDING_SECONDS):
        now = nx_window.open_document(product)
        raise ToolError(
            f"NX answered {message!r}, but {path} is not on disk. Ask the user to look at "
            f"the NX window for a dialog, and do not save again until they have. The "
            f"window shows {now.path if now and now.path else 'no file'} as open."
            + _open_windows_note(product)
        )
    now = _wait_for_title(product, path)
    result: dict[str, Any] = {"applied": True, "status": "done", "file": str(path),
                              "server_message": message, "now_open": now.path if now else None}
    if not _shows(now, path):
        result["warning"] = (
            "The copy is on disk, but the NX window does not show it as the open document."
        )
    return result


def _shows(document: nx_window.Document | None, path: Path) -> bool:
    """Whether the NX title names this file (it has been seen without the extension)."""
    if document is None or document.path is None:
        return False
    shown = os.path.normcase(document.path)
    return shown in {os.path.normcase(str(path)), os.path.normcase(str(path.with_suffix("")))}


def _wait_for_title(product: Product, path: Path) -> nx_window.Document | None:
    """The open document, given the title a few seconds to catch up with a save."""
    document = nx_window.open_document(product)
    for _ in range(12):
        if _shows(document, path):
            break
        time.sleep(0.25)
        document = nx_window.open_document(product)
    return document


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
    refuses ids that exist, "update" ids that do not, except record "1" of the
    one-record /DESIGN tables DCO, DCTL, LLRF, LMRR and SRDF, which have no
    create and are made by their first update. At most 50 records. After applying, the
    table is read back and what actually changed is returned; some tables store
    a new record under an id of their own choosing, so trust "added", not the
    ids you sent. Elements naming a material, section or thickness that does
    not exist are refused (NX would store them pointing at nothing). Any write
    discards the analysis results.
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
    elif not _puts_create(cls, ids):
        missing = [i for i in ids if i not in before]
        if missing:
            hint = "use mode='create' for those" if "POST" in cls.METHODS else (
                "this table has no create, and an update here does not make new records"
            )
            raise ToolError(f"{name} has no ids {missing}; {hint}.")
    if name == "ELEM":
        _check_element_properties(client, payload, before)
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
    elements with them), and so are materials, sections and thicknesses still
    used by an element (NX would leave the elements pointing at nothing).
    Deleting a load case deletes its loads too; the preview says so. At most 50
    ids. The table is read back afterwards: on some /DESIGN tables NX resets
    the record to defaults rather than removing it, or keeps it unchanged, and
    the result says which; records NX removed from other tables are counted.
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
    if name in PROPERTY_TABLES and (users := _users_of(client, name, present)):
        raise ToolError(
            f"{name} ids in use by elements {users}; NX would delete them and leave those "
            "elements pointing at nothing. Give the elements another one first "
            "(write_records on ELEM), or delete the elements."
        )
    # Deleting one record can take others with it; count the tables around it.
    counts = _counts(client, _watched_tables(name))
    token = _token("delete", product, name, present, {k: before[k] for k in present}, counts)
    if confirm_token is None:
        preview = _preview(cls, table=name, will_delete={k: before[k] for k in present},
                           not_found=[k for k in keys if k not in before], confirm_token=token)
        if name in CASCADE_NOTES:
            preview["warning"] = f"{CASCADE_NOTES[name]} {preview['warning']}"
        return preview
    _check_token(confirm_token, token)
    responses = cls.delete([int(k) for k in present], client=client)
    after, _ = _records(cls.get(client=client))
    result: dict[str, Any] = {"applied": True, "table": name, **_diff(before, after),
                              "server_response": _small(responses)}
    gone = set(result["removed"])
    if gone != set(present):
        kept = sorted(set(present) - gone, key=_by_id)
        extra = sorted(gone - set(present), key=_by_id)
        reset = [k for k in kept if k in result["changed"]]
        unchanged = [k for k in kept if k not in result["changed"]]
        notes = [f"ids {reset} are still there, reset by NX (see changed)"] if reset else []
        if unchanged:
            notes.append(f"ids {unchanged} are still there, unchanged: NX did not delete them")
        if extra:
            notes.append(f"ids {extra} are gone too, although they were not asked for")
        result["warning"] = f"Asked to delete {present}: " + "; ".join(notes) + "."
    now = _counts(client, list(counts))
    side = {t: {"before": b, "after": now[t]} for t, b in counts.items()
            if t in now and now[t] != b}
    if side:
        result["also_changed"] = side
    return result


@mcp.tool(annotations=CHANGES_MODEL)
@_guarded
def generate_load_combinations(
    product: Product, kind: CombinationKind, confirm_token: str | None = None
) -> dict:
    """Have NX add its KDS design load combinations. Two steps: preview, then apply.

    kind: "concrete" (KDS 41 20 : 2022, into LCOM-CONC), "steel" (KDS 41 30 :
    2022, LCOM-STEEL) or "src" (KDS 41 SRC : 2022, LCOM-SRC). NX builds the
    combinations from the model's static load cases and their load types and
    appends them (cLCB1, cLCB2, ... for concrete); the existing ones stay.
    Gen NX only: Civil NX has no such command. After applying, the table is
    read back and the combinations NX added are returned. Any change discards
    the analysis results.
    """
    if product != "gen":
        raise ToolError("Generating load combinations is Gen NX only; Civil NX answers 404.")
    generate, code, table = COMBINATION_KINDS[kind]
    cls = catalog.db_tables()[table]
    client = _client(product)
    before, _ = _records(cls.get(client=client))
    cases, _ = _records(catalog.db_tables()["STLD"].get(client=client))
    token = _token("combinations", product, kind, before, cases)
    if confirm_token is None:
        warning = (
            f"NX adds the {code} combinations it derives from these load cases and "
            "their types; how many is NX's choice (hundreds for a model with many wind "
            "and seismic cases). Existing combinations stay. The analysis results are "
            "discarded."
        )
        if before:
            warning += (
                f" {table} already has {len(before)}: NX adds a full new set after them, "
                "so a model that was generated before ends up with both sets."
            )
        return {
            "preview": True,
            "table": table,
            "design_code": code,
            "existing": len(before),
            "existing_names": [r.get("NAME") for r in before.values()][: config.max_rows()],
            "load_cases": {r.get("NAME"): r.get("TYPE") for r in cases.values()},
            "warning": warning,
            "confirm_token": token,
        }
    _check_token(confirm_token, token)
    # ADD only: REPLACE deletes every existing combination first (SDK, live).
    response = generate({"OPTION": "ADD", "DGNCODE": code}, client=client)
    after, _ = _records(cls.get(client=client))
    diff = _diff(before, after)
    # Live: one run added 284 records of ~700 chars each; send their names.
    added = [
        {"id": k, **{f: v[f] for f in ("NAME", "ACTIVE", "DESC") if f in v}}
        for k, v in diff["added"].items()
    ]
    n = config.max_rows()
    result: dict[str, Any] = {
        "applied": True,
        "table": table,
        "added_count": len(added),
        "added": added[:n],
        "changed": list(diff["changed"]),
        "removed": diff["removed"],
        "server_message": _server_message(response),
    }
    if len(added) > n:
        result["note"] = f"{len(added) - n} more added; read_table({table!r}) lists them all"
    if not added:
        result["warning"] = "NX answered, but reading the table back shows no new combination."
    return result


# Prompts: fixed review workflows a client offers as a menu. They only use the
# read tools above; each one says so, so the model does not reach for a write.

READ_ONLY_REVIEW = (
    "This is a review: use read tools only and change nothing in the model. "
    "If something needs fixing, say what and where, and leave the change to the user."
)


@mcp.prompt(title="Check support reactions")
def check_reactions(product: str = "gen", load_case: str = "") -> str:
    """Sum the support reactions and compare them with the model's weight and loads."""
    case = f"the load case {load_case!r}" if load_case else "each static load case"
    return (
        f"Check the support reactions of the {product} model for {case}.\n"
        f"1. check_connection(product={product!r}); read_table(product={product!r}, "
        "table='STLD') for the load case names.\n"
        "2. result_table(table_type='REACTIONG') with load_cases like 'DL(ST)'. If NX says "
        "there is no analysis result, stop and tell the user to run the analysis.\n"
        "3. Sum FX, FY, FZ over all supports. For self-weight and dead load cases compare "
        "the sum of FZ with the weight from result_table(table_type='STORYWEIGHT') or "
        "'ELEMENTWEIGHT'; for lateral cases the horizontal sums should balance the "
        "applied lateral load.\n"
        "4. Report a short table (load case, sum FX/FY/FZ, expected, difference %), and "
        "flag supports with an unusually large share or a reaction in an unexpected "
        "direction.\n" + READ_ONLY_REVIEW
    )


@mcp.prompt(title="Check story drift")
def check_story_drift(allowable_ratio: str = "", load_cases: str = "") -> str:
    """Story drift ratios against an allowable value (Gen NX)."""
    limit = (
        f"the allowable drift ratio {allowable_ratio}"
        if allowable_ratio
        else "the allowable drift ratio (ask the user for it and for the design code "
        "it comes from; do not assume one)"
    )
    cases = f" for {load_cases}" if load_cases else ""
    return (
        f"Check the story drift of the Gen model{cases} against {limit}.\n"
        "1. check_connection(product='gen'); list_result_tables(category='story').\n"
        "2. result_table(product='gen', table_type='STORY_DRIFT_X') and 'STORY_DRIFT_Y'. "
        "If NX says there is no analysis result, tell the user the analysis must be run "
        "with the stories calculated first, and stop.\n"
        "3. For each direction and load case give the largest drift ratio and its story, "
        "and list every story over the limit.\n"
        "4. Report a table (direction, load case, story, drift ratio, limit, OK / NG).\n"
        + READ_ONLY_REVIEW
    )


@mcp.prompt(title="Review the model")
def review_model(product: str = "gen") -> str:
    """A first look at a model: size, units, properties, supports and loads."""
    return (
        f"Give a first review of the {product} model open in NX.\n"
        f"1. check_connection(product={product!r}) and model_summary.\n"
        "2. read_table for MATL, SECT and THIK: list them, and note any that no element "
        "uses (compare with the property ids in read_table('ELEM')). For a big model read "
        "ELEM in parts and say how much was checked.\n"
        "3. result_table(table_type='SUPPORTS'): does the model have supports, and where?\n"
        "4. read_table('STLD') and the load tables that list_tables(search='load') finds: "
        "which load cases have no load assigned?\n"
        "5. result_table(table_type='STORY_MASS') or 'STORYWEIGHT' if the model has stories.\n"
        "6. capture_view for a picture of the model.\n"
        "Report: model size and units; findings ranked by how likely they are mistakes; "
        "what could not be checked and why.\n" + READ_ONLY_REVIEW
    )


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()

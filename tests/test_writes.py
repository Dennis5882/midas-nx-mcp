"""Phase 2 tools against a fake NX (see conftest.py): preview, token, apply, read back."""

import threading
from pathlib import Path

import pytest
import requests
from mcp.server.mcpserver.exceptions import ToolError

from conftest import FakeTable
from midas_nx_mcp import nx_window, server

NODES = {1: {"X": 0, "Y": 0, "Z": 0}, 2: {"X": 8, "Y": 0, "Z": 0}, 3: {"X": 8, "Y": 6, "Z": 0}}
ELEMS = {1: {"TYPE": "BEAM", "NODE": [1, 2, 0, 0, 0, 0, 0, 0]}}


@pytest.fixture
def model(nx):
    nx.writes_allowed = True
    nodes, elems = FakeTable("NODE", NODES), FakeTable("ELEM", ELEMS)
    nx.routes["/db/NODE"] = nodes
    nx.routes["/db/ELEM"] = elems
    return nx, nodes, elems


def _writes(session):
    return [(m, u) for m, u, _ in session.sent if m != "GET"]


# --- write_records ------------------------------------------------------------


def test_preview_changes_nothing_and_returns_a_token(model):
    session, nodes, _ = model
    preview = server.write_records("gen", "NODE", {"4": {"X": 0, "Y": 6, "Z": 0}}, "create")
    assert preview["preview"] and preview["confirm_token"]
    assert _writes(session) == []
    assert "4" not in nodes.records


def test_apply_with_the_token_writes_and_reports_what_changed(model):
    session, _, _ = model
    records = {"4": {"X": 0, "Y": 6, "Z": 0}}
    token = server.write_records("gen", "NODE", records, "create")["confirm_token"]
    result = server.write_records("gen", "NODE", records, "create", confirm_token=token)
    assert result["added"] == {"4": {"X": 0, "Y": 6, "Z": 0}}
    assert result["changed"] == {} and result["removed"] == []
    assert _writes(session)[-1][0] == "POST"
    assert "warning" not in result


@pytest.mark.usefixtures("model")
def test_update_reports_before_and_after():
    records = {"2": {"X": 9, "Y": 0, "Z": 0}}
    token = server.write_records("gen", "NODE", records, "update")["confirm_token"]
    result = server.write_records("gen", "NODE", records, "update", confirm_token=token)
    assert result["changed"] == {"2": {"before": NODES[2], "after": {"X": 9, "Y": 0, "Z": 0}}}


def test_a_wrong_or_stale_token_is_refused(model):
    session, nodes, _ = model
    records = {"2": {"X": 9, "Y": 0, "Z": 0}}
    token = server.write_records("gen", "NODE", records, "update")["confirm_token"]
    nodes.records["2"] = {"X": 7, "Y": 0, "Z": 0}  # the user edits the model meanwhile
    with pytest.raises(ToolError, match="Preview again"):
        server.write_records("gen", "NODE", records, "update", confirm_token=token)
    with pytest.raises(ToolError, match="Preview again"):
        server.write_records("gen", "NODE", records, "update", confirm_token="made-up")
    assert _writes(session) == []


def test_create_refuses_existing_ids_and_update_missing_ones(model):
    with pytest.raises(ToolError, match="mode='update'"):
        server.write_records("gen", "NODE", {"1": {"X": 1}}, "create")
    with pytest.raises(ToolError, match="mode='create'"):
        server.write_records("gen", "NODE", {"99": {"X": 1}}, "update")


def test_renumbered_records_are_flagged(model):
    session, _, _ = model
    session.routes["/db/NODE"] = FakeTable("NODE", NODES, renumber_from=100)
    records = {"4": {"X": 0, "Y": 6, "Z": 0}}
    token = server.write_records("gen", "NODE", records, "create")["confirm_token"]
    result = server.write_records("gen", "NODE", records, "create", confirm_token=token)
    assert list(result["added"]) == ["100"]
    assert "ids other than" in result["warning"]


def test_an_answer_without_a_change_is_not_reported_as_done(model):
    session, _, _ = model
    table = FakeTable("NODE", NODES)
    session.routes["/db/NODE"] = lambda m, u, b: table(m, u, b) if m == "GET" else {"message": ""}
    records = {"4": {"X": 0}}
    token = server.write_records("gen", "NODE", records, "create")["confirm_token"]
    result = server.write_records("gen", "NODE", records, "create", confirm_token=token)
    assert "no change" in result["warning"]


def test_every_write_preview_warns_about_analysis_results(model):
    preview = server.write_records("gen", "NODE", {"4": {"X": 0}}, "create")
    assert "discards the analysis results" in preview["warning"]
    assert "design_note" not in preview
    assert "discards the analysis results" in server.delete_records("gen", "NODE", [3])["warning"]


def test_design_tables_are_written_and_read_back(model):
    session, _, _ = model
    session.routes["/DESIGN/STEEL/DSTL"] = FakeTable("DSTL", {1: {"CODE": "AISC360-16"}})
    records = {"1": {"CODE": "KDS 41 30 : 2022"}}
    preview = server.write_records("gen", "DESIGN/STEEL/DSTL", records, "update")
    assert "DCTL and LENG are one record" in preview["design_note"]
    result = server.write_records(
        "gen", "DESIGN/STEEL/DSTL", records, "update", confirm_token=preview["confirm_token"]
    )
    assert result["changed"]["1"]["after"] == {"CODE": "KDS 41 30 : 2022"}


def test_update_creates_the_record_on_a_put_only_table(model, monkeypatch):
    session, _, _ = model
    name = "DESIGN/RC/KDS-41-20-2022/DCTL"
    assert "POST" not in server.catalog.db_tables()[name].METHODS
    session.routes[f"/{name}"] = FakeTable("DCTL", {})  # a fresh document has none
    records = {"1": {"DT": "XZ"}}
    token = server.write_records("gen", name, records, "update")["confirm_token"]
    result = server.write_records("gen", name, records, "update", confirm_token=token)
    assert result["added"] == {"1": {"DT": "XZ"}}


def test_update_does_not_create_anywhere_else(model):
    session, _, _ = model
    # A PUT-only /DESIGN table with many records, and a PUT-only /db table.
    put_only_db = next(
        n for n, c in server.catalog.db_tables().items()
        if "/db/" in c.ENDPOINT and "POST" not in c.METHODS and "PUT" in c.METHODS
        and "gen" in c.PRODUCTS
    )
    for name in ("DESIGN/RC/KDS-41-20-2022/MEMB", put_only_db):
        cls = server.catalog.db_tables()[name]
        session.routes[cls.ENDPOINT] = FakeTable(name, {})
        with pytest.raises(ToolError, match="has no ids"):
            server.write_records("gen", name, {"9999": {"X": 1}}, "update")
    # A singleton, but not its record 1.
    session.routes["/DESIGN/RC/KDS-41-20-2022/DCTL"] = FakeTable("DCTL", {})
    with pytest.raises(ToolError, match="has no ids"):
        server.write_records("gen", "DESIGN/RC/KDS-41-20-2022/DCTL", {"2": {}}, "update")
    assert _writes(session) == []


def test_a_delete_that_takes_more_than_asked_names_the_extra_ids(model):
    session, nodes, _ = model

    def greedy(m, u, b):
        if m == "DELETE":
            nodes.records.pop("3", None)
            nodes.records.pop("2", None)  # NX takes a neighbour along
            return {}
        return nodes(m, u, b)

    session.routes["/db/NODE"] = greedy
    session.routes["/db/ELEM"] = FakeTable("ELEM", {})
    token = server.delete_records("gen", "NODE", [3])["confirm_token"]
    result = server.delete_records("gen", "NODE", [3], confirm_token=token)
    assert "ids ['2'] are gone too" in result["warning"]


@pytest.mark.parametrize(
    ("after_delete", "expected"),
    [
        ({"1": {"DT": "3D"}}, "reset by NX"),  # DCTL: back to defaults
        ({"1": {"DT": "2D"}}, "did not delete"),  # RC SRDF: "error status", kept
    ],
)
def test_a_design_delete_that_keeps_the_record_says_so(model, after_delete, expected):
    session, _, _ = model
    table = {"1": {"DT": "2D"}}

    def design_table(method, url, body):
        if method == "DELETE":
            table.clear()
            table.update(after_delete)
            return {"message": "error status"}
        return {"DCTL": dict(table)}

    session.routes["/DESIGN/RC/KDS-41-20-2022/DCTL"] = design_table
    name = "DESIGN/RC/KDS-41-20-2022/DCTL"
    token = server.delete_records("gen", name, [1])["confirm_token"]
    result = server.delete_records("gen", name, [1], confirm_token=token)
    assert expected in result["warning"]
    assert result["removed"] == []


def test_big_batches_and_bad_ids_are_refused(model):
    with pytest.raises(ToolError, match="At most 50"):
        server.write_records("gen", "NODE", {str(i): {} for i in range(10, 61)}, "create")
    with pytest.raises(ToolError, match="positive integers"):
        server.write_records("gen", "NODE", {"abc": {}}, "create")


# --- delete_records -----------------------------------------------------------


def test_delete_previews_then_deletes_only_the_listed_ids(model):
    session, nodes, _ = model
    preview = server.delete_records("gen", "NODE", [3, 42])
    assert list(preview["will_delete"]) == ["3"] and preview["not_found"] == ["42"]
    assert _writes(session) == []
    result = server.delete_records("gen", "NODE", [3, 42], confirm_token=preview["confirm_token"])
    assert result["removed"] == ["3"]
    assert _writes(session) == [("DELETE", session.sent[-3][1])]
    assert session.sent[-3][1].endswith("/db/NODE/3")
    assert set(nodes.records) == {"1", "2"}


def test_nodes_used_by_elements_are_not_deleted(model):
    session, _, _ = model
    with pytest.raises(ToolError, match="in use by others"):
        server.delete_records("gen", "NODE", [1])
    assert _writes(session) == []


def test_a_delete_that_takes_other_records_along_is_reported(model):
    session, nodes, elems = model
    def cascade(m, u, b):
        if m == "DELETE":
            nodes.records.clear()  # NX taking the element's nodes with it
        return elems(m, u, b)
    session.routes["/db/ELEM"] = cascade
    token = server.delete_records("gen", "ELEM", [1])["confirm_token"]
    result = server.delete_records("gen", "ELEM", [1], confirm_token=token)
    assert result["also_changed"] == {"NODE": {"before": 3, "after": 0}}


@pytest.fixture
def properties(model):
    session, _, elems = model
    elems.records = {
        "1": {"TYPE": "BEAM", "MATL": 1, "SECT": 7, "NODE": [1, 2, 0, 0, 0, 0, 0, 0]},
        "2": {"TYPE": "PLATE", "MATL": 2, "SECT": 7, "NODE": [1, 2, 3, 0, 0, 0, 0, 0]},
    }
    session.routes["/db/MATL"] = FakeTable("MATL", {1: {"NAME": "C30"}, 2: {"NAME": "C24"}})
    session.routes["/db/SECT"] = FakeTable("SECT", {7: {"SECT_NAME": "B1"}, 8: {"SECT_NAME": "B2"}})
    session.routes["/db/THIK"] = FakeTable("THIK", {7: {"T_IN": 0.2}})
    return session, elems


def test_an_element_naming_a_missing_material_or_section_is_refused(properties):
    session, _ = properties
    beam = {"TYPE": "BEAM", "MATL": 99, "SECT": 98, "NODE": [2, 3, 0, 0, 0, 0, 0, 0]}
    with pytest.raises(ToolError, match="will not exist") as info:
        server.write_records("gen", "ELEM", {"3": beam}, "create")
    assert "record 3 names 99 in MATL" in str(info.value)
    assert "record 3 names 98 in SECT, which /db/SECT" in str(info.value)
    # A plate's SECT is a thickness: THIK 8 does not exist, although SECT 8 does.
    plate = {"TYPE": "PLATE", "MATL": 1, "SECT": 8, "NODE": [1, 2, 3, 0, 0, 0, 0, 0]}
    with pytest.raises(ToolError, match="names 8 in SECT, which /db/THIK will not hold"):
        server.write_records("gen", "ELEM", {"3": plate}, "create")
    # An update is checked against the record it merges into.
    with pytest.raises(ToolError, match="record 1 names 99 in SECT"):
        server.write_records("gen", "ELEM", {"1": {"SECT": 99}}, "update")
    assert _writes(session) == []
    ok = {"TYPE": "BEAM", "MATL": 2, "SECT": 8, "NODE": [2, 3, 0, 0, 0, 0, 0, 0]}
    assert server.write_records("gen", "ELEM", {"3": ok}, "create")["preview"]


@pytest.mark.parametrize(("table", "used_by"), [("MATL", "1"), ("SECT", "1"), ("THIK", "2")])
def test_a_material_section_or_thickness_in_use_is_not_deleted(properties, table, used_by):
    session, _ = properties
    target = 1 if table == "MATL" else 7
    with pytest.raises(ToolError, match="in use by others") as info:
        server.delete_records("gen", table, [target])
    assert f"/db/ELEM record {used_by} (already in the model)" in str(info.value)
    assert _writes(session) == []


def test_an_unused_section_can_be_deleted(properties):
    token = server.delete_records("gen", "SECT", [8])["confirm_token"]
    assert server.delete_records("gen", "SECT", [8], confirm_token=token)["removed"] == ["8"]


def test_deleting_a_load_case_warns_and_counts_the_loads_it_takes(model):
    session, _, _ = model
    cases = FakeTable("STLD", CASES)
    loads = FakeTable("BMLD", {1: {"ITEMS": [{"LCNAME": "DL"}]}})

    def nx_takes_the_loads(method, url, body):
        if method == "DELETE":
            loads.records.clear()
        return cases(method, url, body)

    session.routes["/db/STLD"] = nx_takes_the_loads
    session.routes["/db/BMLD"] = loads
    preview = server.delete_records("gen", "STLD", [1])
    assert preview["warning"].startswith("NX deletes BMLD ['1'] along with it")
    result = server.delete_records("gen", "STLD", [1], confirm_token=preview["confirm_token"])
    assert result["also_changed"] == {"BMLD": {"before": 1, "after": 0}}


def test_deleting_nothing_that_exists_is_refused(model):
    with pytest.raises(ToolError, match="nothing to delete"):
        server.delete_records("gen", "NODE", [77])
    with pytest.raises(ToolError, match="No record ids"):
        server.delete_records("gen", "NODE", [])


# --- run_analysis -------------------------------------------------------------


def _document(monkeypatch, document):
    monkeypatch.setattr(nx_window, "open_document", lambda product: document)


def test_analysis_needs_a_visible_saved_document(model, monkeypatch):
    session, _, _ = model
    _document(monkeypatch, None)
    with pytest.raises(ToolError, match="Cannot see the NX window"):
        server.run_analysis("gen")
    _document(monkeypatch, nx_window.Document(None, True))
    with pytest.raises(ToolError, match="never been saved.*save_copy"):
        server.run_analysis("gen")
    assert _writes(session) == []


def test_analysis_preview_warns_about_the_save_then_runs(model, monkeypatch):
    session, _, _ = model
    _document(monkeypatch, nx_window.Document("C:\\temp\\model", True))
    session.routes["/doc/ANAL"] = {"message": "MIDAS GEN NX command complete"}
    preview = server.run_analysis("gen")
    assert "C:\\temp\\model" in preview["warning"] and "not yet saved" in preview["warning"]
    assert _writes(session) == []
    result = server.run_analysis("gen", confirm_token=preview["confirm_token"])
    assert result["server_message"] == "MIDAS GEN NX command complete"


def test_analysis_refusal_in_a_200_is_an_error(model, monkeypatch):
    session, _, _ = model
    _document(monkeypatch, nx_window.Document("C:\\temp\\model", False))
    session.routes["/doc/ANAL"] = {"message": "MIDAS GEN NX Analysis is not allowed."}
    token = server.run_analysis("gen")["confirm_token"]
    with pytest.raises(ToolError, match="not allowed"):
        server.run_analysis("gen", confirm_token=token)


def test_a_dialog_during_analysis_is_reported_at_once(model, monkeypatch):
    session, _, _ = model
    _document(monkeypatch, nx_window.Document("C:\\temp\\model", False))
    monkeypatch.setattr(server, "DIALOG_POLL_SECONDS", 0.01)
    session.windows = {"Tree Menu"}  # a floating pane that was already open
    answered = threading.Event()

    def blocked_until_answered(method, url, body):
        session.windows.add("Check Changed Story Information")
        answered.wait(5)
        return {"message": "MIDAS GEN NX command complete"}

    session.routes["/doc/ANAL"] = blocked_until_answered
    token = server.run_analysis("gen")["confirm_token"]
    result = server.run_analysis("gen", confirm_token=token)
    answered.set()
    assert result["status"] == "waiting_for_nx_dialog"
    assert result["nx_dialog"] == ["Check Changed Story Information"]
    assert "Do not start the analysis again" in result["message"]


def test_analysis_timeout_says_not_to_run_it_again(model, monkeypatch):
    session, _, _ = model
    _document(monkeypatch, nx_window.Document("C:\\temp\\model", False))
    session.routes["/doc/ANAL"] = requests.Timeout("read timed out")
    token = server.run_analysis("gen")["confirm_token"]
    session.windows = {"Check Changed Story Information"}
    with pytest.raises(ToolError, match="do not start the analysis again") as info:
        server.run_analysis("gen", confirm_token=token)
    assert "Check Changed Story Information" in str(info.value)


# --- save_copy ----------------------------------------------------------------


class Window:
    """The document NX shows; save_as moves it to the copy, as Save As does."""

    def __init__(self, document):
        self.document = document

    def __call__(self, product):
        return self.document


def _nx_saves(window, write_file=True):
    def save_as(method, url, body):
        target = Path(body["Argument"])
        if write_file:
            target.write_bytes(b"model")
            window.document = nx_window.Document(str(target.with_suffix("")), False)
        return {"message": "MIDAS GEN NX command complete"}

    return save_as


def test_save_copy_previews_then_saves_under_a_generated_name(model, monkeypatch, tmp_path):
    session, _, _ = model
    window = Window(nx_window.Document("C:\\work\\bridge", True))
    monkeypatch.setattr(nx_window, "open_document", window)
    session.routes["/doc/SAVEAS"] = _nx_saves(window)
    preview = server.save_copy("gen")
    assert "C:\\work\\bridge stays as it was last saved" in preview["warning"]
    assert "without the changes not yet saved" in preview["warning"]
    assert _writes(session) == []
    result = server.save_copy("gen", confirm_token=preview["confirm_token"])
    saved = Path(result["file"])
    assert saved.parent == tmp_path and saved.name.startswith("mcp-save-gen-")
    assert saved.suffix == ".mgbx" and saved.is_file()
    assert "warning" not in result


def test_save_copy_gives_a_new_document_a_file(model, monkeypatch):
    session, _, _ = model
    window = Window(nx_window.Document(None, True))
    monkeypatch.setattr(nx_window, "open_document", window)
    session.routes["/doc/SAVEAS"] = _nx_saves(window)
    preview = server.save_copy("civil")
    assert "never been saved; the copy becomes its file" in preview["warning"]
    result = server.save_copy("civil", confirm_token=preview["confirm_token"])
    assert result["file"].endswith(".mcbz")
    assert window.document.path is not None


def test_a_save_that_wrote_nothing_is_an_error(model, monkeypatch):
    session, _, _ = model
    window = Window(nx_window.Document("C:\\work\\bridge", False))
    monkeypatch.setattr(nx_window, "open_document", window)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    session.routes["/doc/SAVEAS"] = _nx_saves(window, write_file=False)  # Civil's silent failure
    session.windows = {"Invalid path"}
    token = server.save_copy("gen")["confirm_token"]
    with pytest.raises(ToolError, match="not on disk") as info:
        server.save_copy("gen", confirm_token=token)
    message = str(info.value)
    assert "look at the NX window" in message and "do not save again" in message
    assert "'Invalid path'" in message and "C:\\work\\bridge as open" in message


def test_a_dialog_during_the_save_is_reported_at_once(model, monkeypatch):
    session, _, _ = model
    monkeypatch.setattr(nx_window, "open_document", Window(nx_window.Document("C:\\w\\m", False)))
    monkeypatch.setattr(server, "DIALOG_POLL_SECONDS", 0.01)
    answered = threading.Event()

    def blocked(method, url, body):
        session.windows.add("Save As")
        answered.wait(5)
        return {"message": "MIDAS GEN NX command complete"}

    session.routes["/doc/SAVEAS"] = blocked
    token = server.save_copy("gen")["confirm_token"]
    result = server.save_copy("gen", confirm_token=token)
    answered.set()
    assert result["status"] == "waiting_for_nx_dialog" and result["nx_dialog"] == ["Save As"]


def test_save_copy_token_covers_the_folder_and_preview_checks_it(model, monkeypatch, tmp_path):
    monkeypatch.setattr(nx_window, "open_document", Window(nx_window.Document("C:\\w\\m", False)))
    token = server.save_copy("gen")["confirm_token"]
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("MIDAS_OUTPUT_DIR", str(other))
    with pytest.raises(ToolError, match="Preview again"):
        server.save_copy("gen", confirm_token=token)
    monkeypatch.setenv("MIDAS_OUTPUT_DIR", str(tmp_path / "missing"))
    with pytest.raises(ToolError, match="does not exist"):
        server.save_copy("gen")


@pytest.mark.parametrize("with_extension", [False, True])
def test_a_title_that_catches_up_late_or_shows_the_extension_is_fine(
    model, monkeypatch, with_extension
):
    session, _, _ = model
    original = nx_window.Document("C:\\w\\m", False)
    titles = []

    def window(product):
        return titles.pop(0) if titles else original

    def save_as(method, url, body):
        target = Path(body["Argument"])
        target.write_bytes(b"model")
        shown = str(target) if with_extension else str(target.with_suffix(""))
        # The title still shows the original twice before it moves to the copy.
        titles.extend([original, original, nx_window.Document(shown, False)])
        return {"message": "MIDAS GEN NX command complete"}

    monkeypatch.setattr(nx_window, "open_document", window)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    session.routes["/doc/SAVEAS"] = save_as
    token = server.save_copy("gen")["confirm_token"]
    result = server.save_copy("gen", confirm_token=token)
    assert "warning" not in result, result


def test_save_copy_needs_the_nx_window(model, monkeypatch):
    monkeypatch.setattr(nx_window, "open_document", lambda product: None)
    with pytest.raises(ToolError, match="Cannot see the NX window"):
        server.save_copy("gen")


# --- nx_window ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "path", "dirty"),
    [
        ("MIDAS GEN NX 2026 - [C:\\Temp\\model] - [MIDAS GEN NX]", "C:\\Temp\\model", False),
        ("MIDAS GEN NX 2026 - [C:\\Temp\\model *] - [MIDAS GEN NX]", "C:\\Temp\\model", True),
        ("MIDAS GEN NX 2026 - [\uc81c\ubaa9 \uc5c6\uc74c *] - [MIDAS GEN NX]", None, True),
    ],
)
def test_window_title_gives_the_document(title, path, dirty):
    assert nx_window.parse_title(title) == nx_window.Document(path, dirty)


@pytest.mark.parametrize(
    "title", ["Material Data", "MIDAS GEN NX", "Section Data [1]", "Properties"]
)
def test_dialog_titles_are_not_the_main_window(title):
    assert nx_window.parse_title(title) is None


def test_the_start_page_is_no_project(model, monkeypatch):
    start = nx_window.parse_title("MIDAS GEN NX 2026 - [Start Page]")
    assert start == nx_window.Document(None, False, project_open=False)
    # Told by shape, not words: a localized start page reads the same.
    assert nx_window.parse_title("MIDAS CIVIL NX 2026 - [시작 페이지]") == start
    _document(monkeypatch, start)
    for tool in (server.run_analysis, server.save_copy):
        with pytest.raises(ToolError, match="start page"):
            tool("gen")


# --- generate_load_combinations -----------------------------------------------


CASES = {1: {"NAME": "DL", "TYPE": "D"}, 2: {"NAME": "LL", "TYPE": "L"}}


@pytest.fixture
def combinations(model):
    session = model[0]
    table = FakeTable("LCOM-CONC", {1: {"NAME": "mine"}})
    session.routes["/db/LCOM-CONC"] = table
    session.routes["/db/STLD"] = FakeTable("STLD", CASES)

    def nx_adds_two(method, url, body):
        assert body["Argument"] == {"OPTION": "ADD", "DGNCODE": "KDS 41 20 : 2022"}
        table.records |= {"2": {"NAME": "cLCB1"}, "3": {"NAME": "cLCB2"}}
        return {"message": "MIDAS GEN NX command complete"}

    session.routes["/ope/LCOM-CONC"] = nx_adds_two
    return session, table


def test_combinations_preview_changes_nothing(combinations):
    session, _ = combinations
    preview = server.generate_load_combinations("gen", "concrete")
    assert preview["existing_names"] == ["mine"]
    assert preview["load_cases"] == {"DL": "D", "LL": "L"}
    assert "Existing combinations stay" in preview["warning"]
    assert "already has 1" in preview["warning"]
    assert _writes(session) == []


def test_combinations_are_added_and_read_back(combinations):
    session, _ = combinations
    token = server.generate_load_combinations("gen", "concrete")["confirm_token"]
    result = server.generate_load_combinations("gen", "concrete", confirm_token=token)
    assert result["added"] == [{"id": "2", "NAME": "cLCB1"}, {"id": "3", "NAME": "cLCB2"}]
    assert result["added_count"] == 2
    assert result["removed"] == [] and "warning" not in result
    assert result["server_message"] == "MIDAS GEN NX command complete"
    assert _writes(session) == [("POST", session.sent[-2][1])]


def test_combinations_refuse_a_token_from_other_load_cases(combinations):
    session, _ = combinations
    token = server.generate_load_combinations("gen", "concrete")["confirm_token"]
    session.routes["/db/STLD"] = FakeTable("STLD", {**CASES, 3: {"NAME": "WX", "TYPE": "W"}})
    with pytest.raises(ToolError, match="confirm_token"):
        server.generate_load_combinations("gen", "concrete", confirm_token=token)
    assert _writes(session) == []


def test_combinations_that_add_nothing_are_not_reported_as_done(combinations):
    session, _ = combinations
    session.routes["/ope/LCOM-CONC"] = {"message": "MIDAS GEN NX command complete"}
    token = server.generate_load_combinations("gen", "concrete")["confirm_token"]
    result = server.generate_load_combinations("gen", "concrete", confirm_token=token)
    assert "no new combination" in result["warning"]


def test_combinations_are_refused_on_civil_before_asking_nx(nx):
    with pytest.raises(ToolError, match="Gen NX only"):
        server.generate_load_combinations("civil", "steel")
    assert nx.sent == []


def test_many_new_combinations_are_named_not_dumped(combinations):
    session, table = combinations

    def nx_adds_many(method, url, body):
        table.records |= {str(i): {"NAME": f"cLCB{i}", "vCOMB": [1] * 50} for i in range(10, 20)}
        return {"message": "ok"}

    session.routes["/ope/LCOM-CONC"] = nx_adds_many
    token = server.generate_load_combinations("gen", "concrete")["confirm_token"]
    result = server.generate_load_combinations("gen", "concrete", confirm_token=token)
    assert result["added_count"] == 10 and len(result["added"]) == 3  # MIDAS_MAX_ROWS
    assert "vCOMB" not in result["added"][0]
    assert "7 more added" in result["note"]


# --- references checked by midas_nx.plan.validate -----------------------------


def test_deleting_a_node_or_element_names_the_records_nx_takes_along(model):
    session, _, _ = model
    session.routes["/db/CNLD"] = FakeTable("CNLD", {3: {"ITEMS": []}})
    session.routes["/db/CONS"] = FakeTable("CONS", {3: {"ITEMS": []}})
    session.routes["/db/BMLD"] = FakeTable("BMLD", {1: {"ITEMS": []}})
    session.routes["/db/FRLS"] = FakeTable("FRLS", {1: {"ITEMS": []}})
    node = server.delete_records("gen", "NODE", [3])["warning"]
    assert "NX deletes CNLD ['3'] along with it" in node
    assert "NX deletes CONS ['3'] along with it" in node
    elem = server.delete_records("gen", "ELEM", [1])["warning"]
    assert "NX deletes BMLD ['1'] along with it" in elem
    assert "NX deletes FRLS ['1'] along with it" in elem
    assert _writes(session) == []


def test_a_load_naming_a_load_case_that_does_not_exist_is_refused(model):
    session, _, _ = model
    session.routes["/db/STLD"] = FakeTable("STLD", {1: {"NAME": "DL"}})
    session.routes["/db/BMLD"] = FakeTable("BMLD", {})
    load = {"ITEMS": [{"ID": 1, "LCNAME": "LL", "TYPE": "UNILOAD"}]}
    with pytest.raises(ToolError, match=r"names 'LL' in ITEMS\[\].LCNAME"):
        server.write_records("gen", "BMLD", {"1": load}, "create")
    load["ITEMS"][0]["LCNAME"] = "DL"
    assert server.write_records("gen", "BMLD", {"1": load}, "create")["preview"]
    assert _writes(session) == []


def test_a_create_in_a_table_where_nx_picks_the_ids_says_so(model):
    session, _, _ = model
    session.routes["/db/STLD"] = FakeTable("STLD", {1: {"NAME": "DL"}})
    preview = server.write_records("gen", "STLD", {"2": {"NAME": "LL"}}, "create")
    assert "NX stores new STLD records under ids of its own choosing" in preview["warning"]
    preview = server.write_records("gen", "NODE", {"4": {"X": 0, "Y": 0, "Z": 0}}, "create")
    assert "of its own choosing" not in preview["warning"]


def test_nx_going_away_during_the_checks_is_not_read_as_an_empty_table(model):
    session, _, _ = model
    session.routes["/db/CNLD"] = ({"message": "client does not exist"}, 404)
    with pytest.raises(ToolError, match="client does not exist"):
        server.delete_records("gen", "NODE", [3])
    with pytest.raises(ToolError, match="client does not exist"):
        server.write_records("gen", "CNLD", {"3": {"ITEMS": []}}, "create")
    assert _writes(session) == []

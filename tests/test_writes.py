"""Phase 2 tools against a fake NX (see conftest.py): preview, token, apply, read back."""

import threading

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


def test_design_tables_and_big_batches_are_refused(model):
    with pytest.raises(ToolError, match="read-only"):
        server.write_records("gen", "DESIGN/STEEL/DSTL", {"1": {}}, "update")
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
    with pytest.raises(ToolError, match="in use by elements"):
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
    with pytest.raises(ToolError, match="never been saved"):
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

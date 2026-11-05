"""apply_operations against a fake NX: one preview for many tables, then steps in order."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from conftest import FakeTable
from midas_nx_mcp import server
from midas_nx_mcp.server import Operation

SECTION = {"SECTTYPE": "DBUSER", "SECT_NAME": "C1"}


@pytest.fixture
def empty(nx):
    nx.writes_allowed = True
    tables = {name: FakeTable(name, {}) for name in
              ("MATL", "SECT", "THIK", "NODE", "ELEM", "STLD", "BMLD", "CNLD")}
    for name, table in tables.items():
        nx.routes[f"/db/{name}"] = table
    return nx, tables


def _model():
    """A small model, given in the wrong order on purpose."""
    return [
        Operation(table="BMLD", action="create",
                  records={"1": {"ITEMS": [{"ID": 1, "LCNAME": "DL", "P": [-5, -5, 0, 0]}]}}),
        Operation(table="ELEM", action="create",
                  records={"1": {"TYPE": "BEAM", "MATL": 1, "SECT": 1, "NODE": [1, 2]}}),
        Operation(table="STLD", action="create", records={"1": {"NAME": "DL", "TYPE": "D"}}),
        Operation(table="NODE", action="create",
                  records={"1": {"X": 0, "Y": 0, "Z": 0}, "2": {"X": 0, "Y": 0, "Z": 3}}),
        Operation(table="SECT", action="create", records={"1": SECTION}),
        Operation(table="MATL", action="create", records={"1": {"TYPE": "CONC", "NAME": "C24"}}),
    ]


def _posts(session):
    return [url.rsplit("/db/", 1)[1] for m, url, _ in session.sent if m == "POST"]


def test_preview_orders_checks_and_changes_nothing(empty):
    session, _ = empty
    preview = server.apply_operations("gen", _model())
    assert preview["preview"] and preview["reordered"]
    order = [s["table"] for s in preview["order"]]
    assert order.index("MATL") < order.index("ELEM") and order.index("SECT") < order.index("ELEM")
    assert order.index("NODE") < order.index("ELEM") < order.index("BMLD")
    assert order.index("STLD") < order.index("BMLD")
    assert [m for m, _, _ in session.sent if m != "GET"] == []


def test_apply_runs_every_step_in_order_and_reports_each(empty):
    session, tables = empty
    token = server.apply_operations("gen", _model())["confirm_token"]
    result = server.apply_operations("gen", _model(), confirm_token=token)
    assert result["status"] == "done"
    added = {s["table"]: s["added"] for s in result["steps"]}
    assert added == {"MATL": ["1"], "SECT": ["1"], "NODE": ["1", "2"], "STLD": ["1"],
                     "ELEM": ["1"], "BMLD": ["1"]}
    posts = _posts(session)
    assert posts.index("MATL") < posts.index("ELEM") < posts.index("BMLD")
    assert tables["ELEM"].records["1"]["SECT"] == 1


def test_a_reference_to_a_record_neither_there_nor_created_is_refused(empty):
    session, _ = empty
    ops = _model()
    ops[1] = Operation(table="ELEM", action="create",
                       records={"1": {"TYPE": "BEAM", "MATL": 1, "SECT": 9, "NODE": [1, 2]}})
    with pytest.raises(ToolError, match=r"Step 2 \(create ELEM\): /db/ELEM record 1 names 9 in SECT"):
        server.apply_operations("gen", ops)
    assert [m for m, _, _ in session.sent if m != "GET"] == []


def test_deleting_elements_and_their_material_together_is_allowed(empty):
    _, tables = empty
    tables["MATL"].records = {"1": {"NAME": "C24"}}
    tables["SECT"].records = {"1": SECTION}
    tables["NODE"].records = {"1": {"X": 0}, "2": {"X": 1}}
    tables["ELEM"].records = {"1": {"TYPE": "BEAM", "MATL": 1, "SECT": 1, "NODE": [1, 2]}}
    alone = [Operation(table="MATL", action="delete", ids=[1])]
    with pytest.raises(ToolError, match="in use by others"):
        server.apply_operations("gen", alone)
    both = alone + [Operation(table="ELEM", action="delete", ids=[1])]
    preview = server.apply_operations("gen", both)
    assert [s["table"] for s in preview["order"]] == ["ELEM", "MATL"]
    result = server.apply_operations("gen", both, confirm_token=preview["confirm_token"])
    assert result["status"] == "done" and tables["MATL"].records == {}


def test_ids_are_checked_against_the_model_as_earlier_steps_leave_it(empty):
    ops = [Operation(table="NODE", action="create", records={"1": {"X": 0}}),
           Operation(table="NODE", action="update", records={"1": {"X": 2}})]
    assert server.apply_operations("gen", ops)["preview"]
    with pytest.raises(ToolError, match="do not exist at that point"):
        server.apply_operations("gen", ops[1:])
    with pytest.raises(ToolError, match="already exist"):
        server.apply_operations("gen", ops[:1] * 2)


def test_unit_changes_and_big_batches_are_refused(empty):
    with pytest.raises(ToolError, match="UNIT"):
        server.apply_operations("gen", [Operation(table="UNIT", action="update",
                                                  records={"1": {"DIST": "M"}})])
    many = [Operation(table="NODE", action="create", records={str(i): {"X": i}})
            for i in range(1, 22)]
    with pytest.raises(ToolError, match="At most 20 operations"):
        server.apply_operations("gen", many)
    with pytest.raises(ToolError, match="takes ids"):
        server.apply_operations("gen", [Operation(table="NODE", action="delete",
                                                  records={"1": {}})])


def test_a_stale_token_is_refused(empty):
    session, tables = empty
    token = server.apply_operations("gen", _model())["confirm_token"]
    tables["MATL"].records["5"] = {"NAME": "added in NX meanwhile"}
    with pytest.raises(ToolError, match="Preview again"):
        server.apply_operations("gen", _model(), confirm_token=token)
    assert _posts(session) == []


def test_a_failing_step_stops_the_batch_and_says_what_stays(empty):
    session, tables = empty

    def refuse(method, url, body):
        if method == "POST":
            return {"error": {"message": "[Error] The node no. 9 has not been defined."}}
        return tables["ELEM"](method, url, body)

    session.routes["/db/ELEM"] = refuse
    token = server.apply_operations("gen", _model())["confirm_token"]
    result = server.apply_operations("gen", _model(), confirm_token=token)
    assert result["status"] == "stopped" and result["applied"]
    assert result["failed"]["table"] == "ELEM" and "node no. 9" in result["failed"]["error"]
    assert [s["table"] for s in result["not_run"]] == ["BMLD"]
    assert {s["table"] for s in result["done"]} == {"MATL", "SECT", "NODE", "STLD"}
    assert "nothing was rolled back" in result["why"]
    assert "BMLD" not in _posts(session)


def test_renumbered_records_stop_the_steps_that_name_them(empty):
    session, _ = empty
    session.routes["/db/STLD"] = FakeTable("STLD", {}, renumber_from=7)
    token = server.apply_operations("gen", _model())["confirm_token"]
    result = server.apply_operations("gen", _model(), confirm_token=token)
    assert result["status"] == "stopped"
    assert result["failed"]["table"] == "STLD" and result["failed"]["added"] == ["7"]
    assert "steps [1] name STLD" in result["why"]  # BMLD, given first
    assert "BMLD" not in _posts(session)


def test_deleting_a_load_case_in_a_batch_warns_and_counts_its_loads(empty):
    session, tables = empty
    tables["STLD"].records = {"1": {"NAME": "DL"}}
    tables["BMLD"].records = {"1": {"ITEMS": [{"LCNAME": "DL"}]}}
    cases = tables["STLD"]

    def takes_loads(method, url, body):
        if method == "DELETE":
            tables["BMLD"].records.clear()
        return cases(method, url, body)

    session.routes["/db/STLD"] = takes_loads
    ops = [Operation(table="STLD", action="delete", ids=[1])]
    preview = server.apply_operations("gen", ops)
    assert "Step 1 (delete STLD): NX deletes BMLD ['1'] along with it" in preview["warning"]
    result = server.apply_operations("gen", ops, confirm_token=preview["confirm_token"])
    assert result["steps"][0]["also_changed"]["BMLD"] == {"before": 1, "after": 0}

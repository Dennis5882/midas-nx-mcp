"""The read tools against a fake NX (see conftest.py)."""

import asyncio

import pytest
import requests
from mcp.server.mcpserver.exceptions import ToolError

from conftest import KEY
from midas_nx_mcp import server


def test_check_connection_leaves_out_the_email(nx):
    nx.routes["/mapikey/verify"] = {"status": "connected", "program": "Gen", "email": "a@b.c"}
    result = server.check_connection("gen")
    assert result == {"product": "gen", "status": "connected", "program": "Gen"}


def test_read_table_caps_rows_and_says_so(nx):
    nx.routes["/db/NODE"] = {"NODE": {str(i): {"X": i} for i in range(1, 11)}}
    result = server.read_table("gen", "node")
    assert result["total_in_table"] == 10
    assert result["returned"] == 3
    assert "7 more" in result["note"]


def test_read_table_by_ids_reports_missing_ones(nx):
    nx.routes["/db/NODE"] = {"NODE": {"1": {"X": 0}, "2": {"X": 1}}}
    result = server.read_table("gen", "NODE", ids=[2, 99])
    assert list(result["records"]) == ["2"]
    assert result["missing_ids"] == ["99"]


def test_empty_table_passes_the_server_message_on(nx):
    nx.routes["/db/STLD"] = {"message": "nothing here"}
    result = server.read_table("civil", "STLD")
    assert result["total_in_table"] == 0
    assert result["server_message"] == "nothing here"


def test_unknown_table_suggests_close_names(nx):
    with pytest.raises(ToolError, match="NODE"):
        server.read_table("gen", "NODES")
    assert nx.sent == []


def test_timeout_points_at_the_nx_window(nx):
    nx.routes["/db/NODE"] = requests.Timeout("read timed out")
    with pytest.raises(ToolError, match="NX window") as info:
        server.read_table("gen", "NODE")
    assert KEY not in str(info.value)


def test_error_body_under_200_is_an_error(nx):
    nx.routes["/info/db/NODE"] = {"error": {"message": "refused by NX"}}
    with pytest.raises(ToolError, match="refused by NX"):
        server.describe_table("gen", "NODE")


def test_model_summary_counts_and_survives_one_bad_table(nx):
    nx.routes["/db/NODE"] = {"NODE": {"1": {}, "2": {}}}
    nx.routes["/db/ELEM"] = ({"message": "broken"}, 500)
    nx.routes["/db/UNIT"] = {"UNIT": {"1": {"FORCE": "KN", "DIST": "M"}}}
    result = server.model_summary("gen")
    assert result["counts"]["NODE"] == 2
    assert "broken" in result["counts"]["ELEM"]
    assert result["unit"] == {"1": {"FORCE": "KN", "DIST": "M"}}


def test_result_table_sends_no_export_path_and_caps_rows(nx):
    rows = [[i, 0.0] for i in range(5)]
    nx.routes["/post/TABLE"] = {"Result Table": {"FORCE": "KN", "HEAD": ["Node", "FZ"], "DATA": rows}}
    result = server.result_table("gen", "reactiong", load_cases=["DL(ST)"], keys=[1, 2])
    argument = nx.sent[-1][2]["Argument"]
    assert argument["LOAD_CASE_NAMES"] == ["DL(ST)"]
    assert argument["NODE_ELEMS"] == {"KEYS": [1, 2]}
    assert "OPT_CS" not in argument
    assert result["total_rows"] == 5 and result["returned"] == 3
    assert result["units"] == {"FORCE": "KN"}


def test_result_table_without_a_table_returns_the_message(nx):
    nx.routes["/post/TABLE"] = {"message": "Analysis failed"}
    result = server.result_table("civil", "REACTIONG")
    assert result == {"table_type": "REACTIONG", "rows": 0, "server_message": "Analysis failed"}


def test_result_table_refuses_unknown_types(nx):
    with pytest.raises(ToolError, match="list_result_tables"):
        server.result_table("gen", "NOT_A_TABLE")
    assert nx.sent == []


def test_list_tables_filters_by_product_and_text():
    gen_tables = {row["table"] for row in server.list_tables("gen")}
    assert "NODE" in gen_tables
    assert all("gen" in row["products"] for row in server.list_tables("gen"))
    assert {row["table"] for row in server.list_tables(search="material")} >= {"MATL"}


def test_design_tables_are_read_by_their_path(nx):
    nx.routes["/DESIGN/STEEL/DSTL"] = {"DSTL": {"1": {"CODE": "KDS 41 30 : 2022"}}}
    result = server.read_table("gen", "design/steel/dstl")
    assert result["table"] == "DESIGN/STEEL/DSTL"
    assert result["records"] == {"1": {"CODE": "KDS 41 30 : 2022"}}


def test_design_tables_have_no_schema_to_describe(nx):
    with pytest.raises(ToolError, match="no schema"):
        server.describe_table("gen", "DESIGN/STEEL/DSTL")
    assert nx.sent == []


def test_model_info_caps_entries_keyed_by_id(nx):
    nx.routes["/ope/SECTPROP"] = {"SECTPROP": {str(i): {"DATA": []} for i in range(1, 6)}}
    result = server.read_model_info("gen", "section_properties")
    assert list(result["data"]["SECTPROP"]) == ["1", "2", "3"]
    assert "2 more" in result["note"]


def test_story_parameters_are_refused_on_civil(nx):
    with pytest.raises(ToolError, match="Gen NX only"):
        server.read_model_info("civil", "story_check_parameter")
    assert nx.sent == []


PNG = b"\x89PNG\r\n\x1a\nfake"


def _nx_writes_the_file(method, url, body):
    with open(body["Argument"]["EXPORT_PATH"], "wb") as f:
        f.write(PNG)
    return {"message": "command complete"}


def test_capture_returns_the_image_and_removes_its_file(nx, tmp_path):
    nx.routes["/view/CAPTURE"] = _nx_writes_the_file
    image = server.capture_view("gen", width=800, height=600)
    assert image.data == PNG
    argument = nx.sent[-1][2]["Argument"]
    assert (argument["WIDTH"], argument["HEIGHT"]) == (800, 600)
    assert list(tmp_path.iterdir()) == []


def test_capture_leaves_other_files_alone(nx, tmp_path):
    (tmp_path / "sms_other_app.tmp").write_bytes(b"x")
    nx.routes["/view/CAPTURE"] = _nx_writes_the_file
    server.capture_view("gen")
    assert [p.name for p in tmp_path.iterdir()] == ["sms_other_app.tmp"]


def test_capture_without_a_file_says_nx_may_be_elsewhere(nx, monkeypatch):
    nx.routes["/view/CAPTURE"] = {"message": "command complete"}
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    with pytest.raises(ToolError, match="another computer"):
        server.capture_view("gen")


def test_capture_refuses_a_missing_folder_before_asking_nx(nx, monkeypatch, tmp_path):
    monkeypatch.setenv("MIDAS_OUTPUT_DIR", str(tmp_path / "missing"))
    with pytest.raises(ToolError, match="does not exist"):
        server.capture_view("gen")
    assert nx.sent == []


def test_capture_refuses_oversized_images(nx):
    with pytest.raises(ToolError, match="width"):
        server.capture_view("gen", width=5000)
    assert nx.sent == []


def test_tool_errors_reach_the_client_with_their_text(nx):
    nx.routes["/info/db/NODE"] = {"error": {"message": "refused by NX"}}
    with pytest.raises(ToolError, match="refused by NX"):
        asyncio.run(server.mcp.call_tool("describe_table", {"product": "gen", "table": "NODE"}))

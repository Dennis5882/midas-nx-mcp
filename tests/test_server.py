"""The read tools against a fake NX (see conftest.py)."""

import asyncio

import pytest
import requests
from mcp.server.mcpserver.exceptions import ToolError

from conftest import KEY
from midas_nx_mcp import nx_window, server


def test_check_connection_leaves_out_the_email(nx):
    nx.routes["/mapikey/verify"] = {"status": "connected", "program": "Gen", "email": "a@b.c"}
    result = server.check_connection("gen")
    assert result == {"product": "gen", "status": "connected", "program": "Gen"}


def test_a_closed_nx_is_named_as_such(nx, monkeypatch):
    # Live, Gen NX closed: verify says "disconnected", a read answers 404.
    nx.running = False
    monkeypatch.setattr(nx_window, "open_document", lambda product: None)
    nx.routes["/mapikey/verify"] = {"status": "disconnected", "program": "gen"}
    assert "NX is not running" in server.check_connection("gen")["hint"]
    nx.routes["/db/NODE"] = ({"message": "client does not exist"}, 404)
    with pytest.raises(ToolError, match="404.*NX is not running"):
        server.read_table("gen", "NODE")
    with pytest.raises(ToolError, match="^No Gen NX window.*NX is not running"):
        server.run_analysis("gen")


def test_a_running_nx_with_its_api_off_is_told_apart(nx):
    nx.routes["/mapikey/verify"] = {"status": "disconnected", "program": "gen"}
    assert "is running, but its API" in server.check_connection("gen")["hint"]
    # Off Windows nothing can be seen: neither claim is made.
    nx.running = None
    hint = server.check_connection("gen")["hint"]
    assert "cannot see" in hint and "is running" not in hint


def test_other_nx_errors_pass_unchanged_even_with_nx_closed(nx):
    nx.running = False
    nx.routes["/db/NODE"] = {"error": {"message": "invalid data"}}
    with pytest.raises(ToolError) as caught:
        server.read_table("gen", "NODE")
    assert "invalid data" in str(caught.value)
    assert "not running" not in str(caught.value)


def test_a_failing_window_scan_does_not_hide_the_nx_error(nx, monkeypatch):
    def broken(product):
        raise OSError("EnumWindows failed")

    monkeypatch.setattr(nx_window, "is_running", broken)
    with pytest.raises(ToolError, match="404"):
        server.read_table("gen", "NODE")


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


NO_RESULT = {"error": {"message": "[empty] Cannot generate table data as there is no analysis result."}}


def test_story_tables_without_results_say_what_nx_needs(nx):
    nx.routes["/post/TABLE"] = NO_RESULT
    with pytest.raises(ToolError, match="no analysis result.*stories defined"):
        server.result_table("gen", "STORY_DRIFT_X")


def test_other_tables_without_results_keep_the_nx_message_alone(nx):
    nx.routes["/post/TABLE"] = NO_RESULT
    with pytest.raises(ToolError, match="no analysis result") as raised:
        server.result_table("gen", "REACTIONG")
    assert "stories" not in str(raised.value)


def test_result_tables_are_grouped_with_what_each_group_needs():
    listing = server.list_result_tables()
    assert set(listing["categories"]) == {"model", "analysis", "story", "design"}
    by_type = {t["table_type"]: t["category"] for t in listing["tables"]}
    assert by_type["REACTIONG"] == "analysis"
    assert by_type["STORY_DRIFT_X"] == "story"
    assert by_type["STORY_MASS"] == "model"
    assert by_type["BEAMDESIGNFORCES"] == "design"
    story = server.list_result_tables(category="story")
    assert set(story["categories"]) == {"story"}
    assert all(t["category"] == "story" for t in story["tables"])
    assert server.list_result_tables(search="drift", category="model")["tables"] == []


def test_prompts_name_only_tools_that_exist_and_change_nothing():
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    writes = {"run_analysis", "save_copy", "write_records", "delete_records"}
    prompts = asyncio.run(server.mcp.list_prompts())
    assert {p.name for p in prompts} == {"check_reactions", "check_story_drift", "review_model"}
    for prompt in prompts:
        result = asyncio.run(server.mcp.get_prompt(prompt.name, {}))
        text = " ".join(m.content.text for m in result.messages)
        named = {word.split("(")[0] for word in text.replace(",", " ").split() if "(" in word}
        assert named & tools, prompt.name
        assert not named & writes, prompt.name
        assert "change nothing" in text


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


def _graphic(nx) -> dict:
    return next(body["Argument"] for _, url, body in nx.sent if url.endswith("/view/RESULTGRAPHIC"))


REACTIONS = {"Result Table": {"FORCE": "KN", "HEAD": ["Node", "FZ"], "DATA": [[1, 10.0]]}}


def test_show_result_sets_the_graphic_then_captures(nx):
    nx.routes["/post/TABLE"] = REACTIONS
    nx.routes["/view/RESULTGRAPHIC"] = {"message": "MIDAS GEN NX command complete"}
    nx.routes["/view/CAPTURE"] = _nx_writes_the_file
    image = server.show_result("gen", "beam_diagram", "DL", component="my", width=800)
    assert image.data == PNG
    assert [url.rsplit("/", 1)[1] for _, url, _ in nx.sent] == ["TABLE", "RESULTGRAPHIC", "CAPTURE"]
    # The result check asks for the same case, in result_table's spelling.
    assert nx.sent[0][2]["Argument"]["LOAD_CASE_NAMES"] == ["DL(ST)"]
    argument = _graphic(nx)
    assert argument["CURRENT_MODE"] == "beamdiagrams"
    assert argument["LOAD_CASE_COMB"] == {"TYPE": "ST", "NAME": "DL"}
    assert argument["COMPONENTS"] == {"COMP": "My"}  # NX's spelling
    assert argument["TYPE_OF_DISPLAY"]["CONTOUR"] == {"OPT_CHECK": True}
    assert nx.sent[-1][2]["Argument"]["WIDTH"] == 800


def test_show_result_takes_combinations_with_their_type(nx):
    nx.routes["/view/RESULTGRAPHIC"] = {"message": "ok"}
    nx.routes["/view/CAPTURE"] = _nx_writes_the_file
    server.show_result("gen", "reactions", " cLCB1(cbc) ")
    argument = _graphic(nx)
    assert argument["LOAD_CASE_COMB"] == {"TYPE": "CBC", "NAME": "cLCB1"}
    assert argument["COMPONENTS"] == {"COMP": "FZ"}
    assert argument["TYPE_OF_DISPLAY"]["VALUES"] == {"OPT_CHECK": True}


def test_show_result_scales_the_deformed_shape(nx):
    nx.routes["/view/RESULTGRAPHIC"] = {"message": "ok"}
    nx.routes["/view/CAPTURE"] = _nx_writes_the_file
    server.show_result("gen", "deformed_shape", "WX", deform_scale=2.5)
    display = _graphic(nx)["TYPE_OF_DISPLAY"]
    assert display["DEFORM"] == {"OPT_CHECK": True, "SCALE_FACTOR": 2.5}
    assert "CONTOUR" not in display


def test_show_result_names_the_components_nx_refused(nx):
    nx.routes["/view/RESULTGRAPHIC"] = {"error": {"message": "MIDAS GEN NX second query is wrong"}}
    with pytest.raises(ToolError, match="known ones: Mxx, Myy"):
        server.show_result("gen", "plate_forces", "DL", component="Q")
    assert not any(url.endswith("/view/CAPTURE") for _, url, _ in nx.sent)


def test_show_result_passes_a_missing_load_case_on(nx):
    nx.routes["/view/RESULTGRAPHIC"] = {"error": {"message": "MIDAS GEN NX Can not find load case."}}
    with pytest.raises(ToolError, match="Can not find load case"):
        server.show_result("gen", "displacement", "NOPE")


def test_show_result_refuses_when_there_are_no_results(nx):
    # Live: NX would draw the bare model and still say "command complete".
    nx.routes["/post/TABLE"] = NO_RESULT
    nx.routes["/view/RESULTGRAPHIC"] = {"message": "MIDAS GEN NX command complete"}
    with pytest.raises(ToolError, match="no analysis result.*run the analysis first"):
        server.show_result("gen", "reactions", "DL")
    assert not any(url.endswith(("/view/RESULTGRAPHIC", "/view/CAPTURE")) for _, url, _ in nx.sent)


def test_show_result_leaves_other_check_failures_to_nx(nx):
    nx.routes["/post/TABLE"] = {"error": {"message": "MIDAS GEN NX Can not find load case."}}
    nx.routes["/view/RESULTGRAPHIC"] = {"message": "ok"}
    nx.routes["/view/CAPTURE"] = _nx_writes_the_file
    assert server.show_result("gen", "reactions", "DL").data == PNG


def test_show_result_refuses_bad_arguments_before_asking_nx(nx):
    with pytest.raises(ToolError, match="deform_scale"):
        server.show_result("gen", "deformed_shape", "DL", deform_scale=0)
    with pytest.raises(ToolError, match="load_case is empty"):
        server.show_result("gen", "reactions", "  ")
    with pytest.raises(ToolError, match="width"):
        server.show_result("gen", "reactions", "DL", width=9000)
    assert nx.sent == []


def test_tool_errors_reach_the_client_with_their_text(nx):
    nx.routes["/info/db/NODE"] = {"error": {"message": "refused by NX"}}
    with pytest.raises(ToolError, match="refused by NX"):
        asyncio.run(server.mcp.call_tool("describe_table", {"product": "gen", "table": "NODE"}))

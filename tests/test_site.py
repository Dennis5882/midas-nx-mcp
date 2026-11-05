"""The docs and the public page name every tool the server offers.

A tool added to server.py without its page entries fails here, instead of the
page quietly showing an old count (it said 15 for a while after 0.3.2).
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("site_tools", ROOT / "scripts" / "site_tools.py")
site_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tools)

TOOLS = site_tools.server_tools()


def test_every_tool_is_placed_on_the_page():
    site_tools.check_groups(TOOLS)


@pytest.mark.parametrize("page", site_tools.PAGES, ids=lambda p: p.parent.name)
def test_page_lists_and_counts_are_current(page):
    html = page.read_text("utf-8")
    assert site_tools.render(html, TOOLS) == html, "run: python scripts/site_tools.py"


@pytest.mark.parametrize("doc", [
    "README.md", "site/llms.txt", "site/en/llms.txt", "docs/user_guide_ko.md",
    "scripts/wheel_smoke_test.py",
])
def test_doc_names_every_tool(doc):
    text = (ROOT / doc).read_text("utf-8")
    missing = sorted(name for name in TOOLS if name not in text)
    assert not missing, f"{doc} does not mention {missing}"

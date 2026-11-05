"""Keep the public page's tool lists and counts in step with the server.

The page lists every tool by name under its feature card and shows how many
tools there are. Both come from the running server's tool list, so a new tool
cannot leave the page behind:

    python scripts/site_tools.py           # rewrite site/index.html and site/en/index.html
    python scripts/site_tools.py --check   # exit 1 if they are out of date (tests run this)

A tool has to be placed in GROUPS by hand; an unplaced or unknown tool is an
error, so the page never silently drops one.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGES = (ROOT / "site" / "index.html", ROOT / "site" / "en" / "index.html")

# Feature card (data-tools="...") -> the tools listed under it, in this order.
GROUPS = {
    "read": ("model_summary", "read_table", "describe_table", "list_tables",
             "read_model_info", "check_connection"),
    "results": ("result_table", "list_result_tables"),
    "view": ("show_result", "capture_view"),
    "change": ("write_records", "delete_records", "apply_operations",
               "generate_load_combinations", "run_analysis", "save_copy"),
}


def server_tools() -> dict[str, bool]:
    """Every tool the server offers -> whether it changes the model or its file."""
    sys.path.insert(0, str(ROOT / "src"))
    from midas_nx_mcp import server

    tools = asyncio.run(server.mcp.list_tools())
    # READ_ONLY and WRITES_FILE are idempotent; CHANGES_MODEL and SWITCHES_FILE are not.
    return {t.name: not t.annotations.idempotent_hint for t in tools}


def counts(tools: dict[str, bool]) -> dict[str, int]:
    changes = sum(tools.values())
    return {"all": len(tools), "read": len(tools) - changes, "change": changes}


def check_groups(tools: dict[str, bool]) -> None:
    placed = [name for names in GROUPS.values() for name in names]
    missing = sorted(set(tools) - set(placed))
    unknown = sorted(set(placed) - set(tools))
    twice = sorted({n for n in placed if placed.count(n) > 1})
    if missing or unknown or twice:
        raise SystemExit(f"GROUPS is out of date: not placed {missing}, not a tool {unknown}, twice {twice}")
    wrong = [n for n in GROUPS["change"] if not tools[n]] + [
        n for g, names in GROUPS.items() if g != "change" for n in names if tools[n]]
    if wrong:
        raise SystemExit(f"tools in the wrong group (change vs. read): {wrong}")


def render(html: str, tools: dict[str, bool]) -> str:
    def meta(m: re.Match) -> str:
        names = "".join(f"<span>{name}</span>" for name in GROUPS[m.group(2)])
        return f"{m.group(1)}{names}</div>"

    html, n = re.subn(r'(<div class="meta" data-tools="(\w+)">).*?</div>', meta, html)
    if n != len(GROUPS):
        raise SystemExit(f"expected {len(GROUPS)} data-tools lists, found {n}")
    for key, value in counts(tools).items():
        html, n = re.subn(rf'(<b data-count="{key}">)\d+', rf"\g<1>{value}", html)
        if n != 1:
            raise SystemExit(f'expected one data-count="{key}", found {n}')
    return html


def main() -> int:
    tools = server_tools()
    check_groups(tools)
    stale = []
    for page in PAGES:
        old = page.read_text("utf-8")
        new = render(old, tools)
        if new != old:
            stale.append(page.relative_to(ROOT).as_posix())
            if "--check" not in sys.argv:
                page.write_text(new, "utf-8")
    if stale and "--check" in sys.argv:
        print(f"out of date: {', '.join(stale)}; run python scripts/site_tools.py", file=sys.stderr)
        return 1
    print("updated: " + ", ".join(stale) if stale else "up to date", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

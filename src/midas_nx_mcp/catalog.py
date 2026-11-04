"""What the server knows about, taken from midas-nx itself.

The /db and /DESIGN tables are the SDK's DbResource classes and the result
tables are its TABLE_TYPE_* constants, so a new SDK release widens what the
tools accept without any change here.
"""

from __future__ import annotations

import importlib
from functools import cache

from midas_nx.db import all_resources
from midas_nx.db.base import DbResource

_POST_MODULES = ("pre_process", "result_1", "story", "design")


def table_name(endpoint: str) -> str:
    """``/db/NODE`` -> ``NODE``; ``/DESIGN/STEEL/DSTL`` -> ``DESIGN/STEEL/DSTL``.

    Design tables keep their path: the same short name (DCO, DCTL, ...) exists
    under several design codes.
    """
    return endpoint.removeprefix("/db/").lstrip("/")


@cache
def db_tables() -> dict[str, type[DbResource]]:
    """``{"NODE": Node, "DESIGN/STEEL/DSTL": ..., ...}`` for every table that answers GET."""
    # The SDK's own list (2.10.7+), held equal to the npm package's: walking
    # the modules here once missed db.properties (MATL/SECT/THIK).
    return {
        table_name(endpoint): cls
        for endpoint, cls in all_resources().items()
        if "GET" in cls.METHODS
    }


@cache
def result_table_types() -> dict[str, str]:
    """``{"REACTIONG": "result_1.REACTION_GLOBAL", ...}`` for /post/TABLE."""
    types = {}
    for name in _POST_MODULES:
        module = importlib.import_module(f"midas_nx.post.{name}")
        for attr, value in vars(module).items():
            if attr.startswith("TABLE_TYPE_") and isinstance(value, str):
                types.setdefault(value, f"{name}.{attr.removeprefix('TABLE_TYPE_')}")
    return dict(sorted(types.items()))

"""What the server knows about, taken from midas-nx itself.

The /db and /DESIGN tables are the SDK's DbResource classes and the result
tables are its TABLE_TYPE_* constants, so a new SDK release widens what the
tools accept without any change here.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import cache

import midas_nx.db
import midas_nx.design
from midas_nx.db.base import DbResource

_POST_MODULES = ("pre_process", "result_1", "story", "design")


def _subclasses(cls: type) -> list[type]:
    found = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found


def table_name(endpoint: str) -> str:
    """``/db/NODE`` -> ``NODE``; ``/DESIGN/STEEL/DSTL`` -> ``DESIGN/STEEL/DSTL``.

    Design tables keep their path: the same short name (DCO, DCTL, ...) exists
    under several design codes.
    """
    return endpoint.removeprefix("/db/").lstrip("/")


@cache
def db_tables() -> dict[str, type[DbResource]]:
    """``{"NODE": Node, "DESIGN/STEEL/DSTL": ..., ...}`` for every table that answers GET."""
    # Subpackages too (db.properties holds MATL/SECT/THIK), and design/, which
    # defines the /DESIGN design-parameter tables.
    for package in (midas_nx.db, midas_nx.design):
        for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
            importlib.import_module(module.name)
    tables = {}
    for cls in _subclasses(DbResource):
        endpoint = getattr(cls, "ENDPOINT", "")
        if endpoint.startswith(("/db/", "/DESIGN/")) and "GET" in cls.METHODS:
            tables[table_name(endpoint)] = cls
    return dict(sorted(tables.items()))


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

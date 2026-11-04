"""A fake NX: a real MidasClient over a scripted HTTP session."""

import json

import pytest
from midas_nx import MidasClient

from midas_nx_mcp import nx_window, server

KEY = "secret-mapi-key"


class FakeResponse:
    def __init__(self, body, status=200):
        self.text = json.dumps(body)
        self.status_code = status
        self.ok = status < 400
        self.reason = "fake"
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    """Answers by path (``/db/NODE`` also answers ``/db/NODE/3``); records every request.

    A callable answer is called with (method, url, body).
    """

    def __init__(self):
        self.routes = {}
        self.sent = []
        self.writes_allowed = False

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.sent.append((method, url, json))
        for path, answer in self.routes.items():
            if url.endswith(path) or f"{path}/" in url:
                if isinstance(answer, Exception):
                    raise answer
                if callable(answer):
                    answer = answer(method, url, json)
                return FakeResponse(*answer) if isinstance(answer, tuple) else FakeResponse(answer)
        return FakeResponse({"message": "no route"}, 404)


class FakeTable:
    """One /db table that GET, POST, PUT and per-id DELETE really change."""

    def __init__(self, name, records, renumber_from=None):
        self.name = name
        self.records = {str(k): v for k, v in records.items()}
        self.renumber_from = renumber_from  # store new records from this id on

    def __call__(self, method, url, body):
        if method == "GET":
            return {self.name: dict(self.records)}
        if method == "DELETE":
            self.records.pop(url.rsplit("/", 1)[1], None)
            return {}
        for key, value in body["Assign"].items():
            if method == "POST" and self.renumber_from is not None:
                key = str(self.renumber_from)
                self.renumber_from += 1
            self.records[key] = value
        return {self.name: body["Assign"]}


@pytest.fixture
def nx(monkeypatch, tmp_path):
    session = FakeSession()
    monkeypatch.setattr(
        server,
        "_client",
        lambda product, timeout=None: MidasClient(KEY, product=product, session=session),
    )
    monkeypatch.setenv("MIDAS_MAX_ROWS", "3")
    monkeypatch.setenv("MIDAS_OUTPUT_DIR", str(tmp_path))
    # Not the windows of a real NX on this PC; tests set their own.
    session.windows = set()
    session.running = True
    monkeypatch.setattr(nx_window, "other_windows", lambda product: set(session.windows))
    monkeypatch.setattr(nx_window, "is_running", lambda product: session.running)
    yield session
    for method, url, body in session.sent:
        # Never, in any test: a DELETE of a whole table, or /doc/NEW|OPEN|IMPORT*.
        if method == "DELETE":
            assert url.rsplit("/", 1)[1].isdigit(), url
        assert not any(f"/doc/{c}" in url for c in ("NEW", "OPEN", "IMPORT")), url
        # A save only ever goes to a generated name in the output folder.
        if url.endswith("/doc/SAVEAS"):
            target = body["Argument"]
            target = target.get("PATH", target) if isinstance(target, dict) else target
            assert str(target).startswith(tmp_path.as_posix() + "/mcp-save-"), target
        if method == "GET" or session.writes_allowed:
            continue
        # Otherwise nothing changes the model: /post/TABLE without EXPORT_PATH,
        # /view/CAPTURE, only into the output folder under a generated name, and
        # /view/RESULTGRAPHIC, which changes what the window shows and nothing else.
        assert method == "POST"
        argument = body["Argument"]
        if url.endswith("/view/CAPTURE"):
            assert argument["EXPORT_PATH"].startswith(tmp_path.as_posix() + "/mcp-capture-")
        elif url.endswith("/view/RESULTGRAPHIC"):
            assert "EXPORT_PATH" not in argument
        else:
            assert url.endswith("/post/TABLE")
            assert "EXPORT_PATH" not in argument

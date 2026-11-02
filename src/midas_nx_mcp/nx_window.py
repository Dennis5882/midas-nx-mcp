"""Which document NX has open, read from its window title.

The API has no call for this, and it matters before /doc/ANAL: NX saves the
open document first, and an unsaved new one raises a Save As dialog that
blocks the session. The title reads like
``MIDAS GEN NX 2026 - [C:\\Temp\\model] - [MIDAS GEN NX]``, with `` *`` after
the path while there are unsaved changes and no path for a new document.

Only works when NX runs on this PC; elsewhere it finds nothing.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

EXECUTABLES = {"gen": "gennx.exe", "civil": "cvlw.exe"}
_DOCUMENT = re.compile(r"\[([^\]]*)\]")
_SAVED_PATH = re.compile(r"^[A-Za-z]:\\|^\\\\")


@dataclass(frozen=True)
class Document:
    path: str | None  # None for a document that was never saved
    unsaved_changes: bool


def parse_title(title: str) -> Document | None:
    match = _DOCUMENT.search(title)
    if not match:
        return None
    text = match.group(1).strip()
    dirty = text.endswith("*")
    text = text.rstrip("*").strip()
    return Document(text if _SAVED_PATH.match(text) else None, dirty)


def window_titles(product: str) -> list[str]:
    """Titles of the visible top-level windows of that product's process."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    exe = EXECUTABLES[product]
    titles: list[str] = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if handle:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            found = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            if found and buf.value.lower().endswith(exe):
                length = user32.GetWindowTextLengthW(hwnd)
                text = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, text, length + 1)
                if text.value:
                    titles.append(text.value)
            kernel32.CloseHandle(handle)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return titles


def other_windows(product: str) -> set[str]:
    """Titles of the product's windows other than its main one: dialogs, and
    floating panes. Compare against an earlier call to tell a new dialog."""
    return {t for t in window_titles(product) if parse_title(t) is None}


def open_document(product: str) -> Document | None:
    """The document in the product's main window, or None if no window is found."""
    for title in window_titles(product):
        document = parse_title(title)
        if document is not None:
            return document
    return None

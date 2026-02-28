from __future__ import annotations

from typing import Any

_state: dict[str, Any] = {}


def get_db():
    return _state["db"]


def get_wix():
    return _state["wix"]


def get_eleven():
    return _state["eleven"]


def get_xapi():
    return _state["xapi"]


def get_google():
    return _state["google"]


def get_imessage():
    return _state["imessage"]


def get_settings():
    return _state["settings"]


def get_root():
    return _state["root"]

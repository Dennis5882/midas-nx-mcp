import pytest

from midas_nx_mcp import config


def test_unknown_product_is_refused():
    with pytest.raises(ValueError):
        config.mapi_key("other")


def test_environment_wins_over_env_file(monkeypatch):
    monkeypatch.setenv("MIDAS_MAPI_KEY_GEN", "from-env")
    assert config.mapi_key("gen") == "from-env"


def test_missing_key_gives_a_hint(monkeypatch):
    monkeypatch.setenv("MIDAS_MAPI_KEY_CIVIL", "")
    monkeypatch.setattr(config, "_read_env_file", dict)
    with pytest.raises(ValueError, match="Hint"):
        config.mapi_key("civil")

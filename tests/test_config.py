import pytest

from midas_nx_mcp import config


def test_unknown_product_is_refused():
    with pytest.raises(ValueError):
        config.mapi_key("other")


def test_environment_wins_over_env_file(monkeypatch):
    monkeypatch.setenv("MIDAS_MAPI_KEY_GEN", "from-env")
    assert config.mapi_key("gen") == "from-env"


def test_installed_package_reads_no_env_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("MIDAS_MAPI_KEY_GEN=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)  # no pyproject.toml here
    monkeypatch.delenv("MIDAS_MAPI_KEY_GEN", raising=False)
    assert config.setting("MIDAS_MAPI_KEY_GEN") == ""
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert config.setting("MIDAS_MAPI_KEY_GEN") == "from-file"


def test_missing_key_gives_a_hint(monkeypatch):
    monkeypatch.setenv("MIDAS_MAPI_KEY_CIVIL", "")
    monkeypatch.setattr(config, "_read_env_file", dict)
    with pytest.raises(ValueError, match="Hint"):
        config.mapi_key("civil")


@pytest.mark.parametrize("value", ["", "  ", "${user_config.output_dir}"])
def test_a_blank_bundle_option_falls_back_to_the_default(monkeypatch, value):
    # A .mcpb passes options the user left blank as empty, or as the placeholder.
    monkeypatch.setenv("MIDAS_OUTPUT_DIR", value)
    monkeypatch.setattr(config, "_read_env_file", dict)
    assert config.output_dir().as_posix() == "C:/temp"

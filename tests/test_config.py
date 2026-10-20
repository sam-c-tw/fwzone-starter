import pytest

from fwzone.config import Config, load_config
from fwzone.errors import InputError, UsageError


def write(tmp_path, text):
    path = tmp_path / "fwzone.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_without_file():
    config = load_config(None)
    assert config == Config()
    assert config.rules.sheet == "rule"
    assert config.rules.source_column == "source_grp"
    assert config.groups.member_column == "segment"
    assert config.zones.zone_column == "zone"
    assert config.parsing.delimiters == ("\n", ",", ";")
    assert config.parsing.any_keywords == ("any",)
    assert config.output.zone_separator == ", "


def test_partial_override_keeps_other_defaults(tmp_path):
    path = write(
        tmp_path,
        '[rules]\nsource_column = "src"\n[parsing]\ndelimiters = ["\\n", "|"]\n'
        '[output]\nzone_separator = " / "\n',
    )
    config = load_config(path)
    assert config.rules.source_column == "src"
    assert config.rules.destination_column == "destination_grp"
    assert config.parsing.delimiters == ("\n", "|")
    assert config.output.zone_separator == " / "


def test_invalid_toml_is_usage_error(tmp_path):
    with pytest.raises(UsageError):
        load_config(write(tmp_path, "[rules\nsheet ="))


@pytest.mark.parametrize(
    "text",
    [
        "[rules]\nsheet = 1\n",
        '[parsing]\ndelimiters = "x"\n',
        "[parsing]\ndelimiters = [1]\n",
        '[parsing]\ndelimiters = [""]\n',
        'rules = "x"\n',
        '[rules]\nsource_column = ""\n',
        '[output]\nsource_zone_column = "Z"\ndestination_zone_column = "Z"\n',
    ],
)
def test_invalid_values_are_usage_error(tmp_path, text):
    with pytest.raises(UsageError):
        load_config(write(tmp_path, text))


def test_missing_config_file(tmp_path):
    with pytest.raises(InputError):
        load_config(tmp_path / "nope.toml")

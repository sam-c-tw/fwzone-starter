"""讀取 TOML 設定檔、套預設值、驗證（SPEC 3.5）。"""

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from fwzone.errors import InputError, UsageError


@dataclass(frozen=True)
class RulesConfig:
    sheet: str = "rule"
    source_column: str = "source_grp"
    destination_column: str = "destination_grp"


@dataclass(frozen=True)
class GroupsConfig:
    sheet: str = "group"
    name_column: str = "group_name"
    member_column: str = "segment"


@dataclass(frozen=True)
class ZonesConfig:
    sheet: str = "segment"
    cidr_column: str = "segment"
    zone_column: str = "zone"


@dataclass(frozen=True)
class ParsingConfig:
    delimiters: tuple[str, ...] = ("\n", ",", ";")
    any_keywords: tuple[str, ...] = ("any",)


@dataclass(frozen=True)
class OutputConfig:
    source_zone_column: str = "Source Zone"
    destination_zone_column: str = "Destination Zone"
    zone_separator: str = ", "


@dataclass(frozen=True)
class Config:
    rules: RulesConfig = field(default_factory=RulesConfig)
    groups: GroupsConfig = field(default_factory=GroupsConfig)
    zones: ZonesConfig = field(default_factory=ZonesConfig)
    parsing: ParsingConfig = field(default_factory=ParsingConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


_SECTIONS: dict[str, type] = {
    "rules": RulesConfig,
    "groups": GroupsConfig,
    "zones": ZonesConfig,
    "parsing": ParsingConfig,
    "output": OutputConfig,
}


def _build_section(name: str, cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        raise UsageError(f"設定檔的 [{name}] 必須是表格（table）")
    defaults = cls()
    kwargs: dict[str, Any] = {}
    # TODO(spec): 設定檔中未定義的區段或欄位未規定處理方式，目前忽略
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        where = f"設定檔的 [{name}] {f.name}"
        if isinstance(getattr(defaults, f.name), tuple):
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise UsageError(f"{where} 必須是非空字串的陣列")
            kwargs[f.name] = tuple(value)
        else:
            if not isinstance(value, str):
                raise UsageError(f"{where} 必須是字串")
            if not value and f.name != "zone_separator":
                raise UsageError(f"{where} 不可為空字串")
            kwargs[f.name] = value
    return cls(**kwargs)


def load_config(path: Path | None) -> Config:
    """未提供路徑時全部使用預設值。格式錯誤拋出 UsageError。"""
    if path is None:
        return Config()
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        # TODO(spec): 設定檔不存在的結束碼未定義，目前視為輸入資料錯誤（1）
        raise InputError(f"找不到設定檔：{path}") from exc
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise UsageError(f"設定檔格式錯誤（{path}）：{exc}") from exc

    config = Config(**{n: _build_section(n, c, data.get(n, {})) for n, c in _SECTIONS.items()})
    if config.output.source_zone_column == config.output.destination_zone_column:
        raise UsageError("設定檔的 [output] source_zone_column 與 destination_zone_column 不可相同")
    return config

"""读取六字段 YAML 供应商配置。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import yaml

from mycode.errors import ConfigError


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai"]
    model: str
    base_url: str
    api_key: str = field(repr=False)
    thinking: bool = False


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise ConfigError("配置包含重复或非法的字段键。")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _base_url(value: str, protocol: str) -> str:
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in ("http", "https")
            and parts.hostname
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and not any(c.isspace() for c in value)
        )
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("base_url 必须是有效的 HTTP/HTTPS 基础地址，不能含认证或查询信息。")
    path = parts.path.rstrip("/")
    if path.endswith(("/messages", "/chat/completions")):
        raise ConfigError("base_url 应填写 API 基础地址，不能填写完整请求端点。")
    if protocol == "anthropic" and path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def load_config(path: Path) -> tuple[ProviderConfig, ...]:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueSafeLoader)
    except ConfigError:
        raise
    except (OSError, UnicodeError):
        raise ConfigError("无法读取配置文件，请检查 --config 指定的路径与权限。") from None
    except yaml.YAMLError:
        raise ConfigError("YAML 配置格式错误，请检查缩进和字段格式。") from None
    if not isinstance(data, list) or not data:
        raise ConfigError("配置顶层必须是非空供应商列表。")
    required = {"name", "protocol", "model", "base_url", "api_key"}
    result = []
    names = set()
    for index, entry in enumerate(data, 1):
        prefix = f"第 {index} 个供应商："
        if not isinstance(entry, dict):
            raise ConfigError(prefix + "必须是字段映射。")
        if required - entry.keys() or entry.keys() - (required | {"thinking"}):
            raise ConfigError(prefix + "必填字段不完整或包含未知字段。")
        for key in required:
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise ConfigError(prefix + f"{key} 必须是非空字符串。")
        if entry["protocol"] not in ("anthropic", "openai"):
            raise ConfigError(prefix + "protocol 必须为 anthropic 或 openai。")
        if entry["name"] in names:
            raise ConfigError(prefix + "name 重复。")
        thinking = entry.get("thinking", False)
        if not isinstance(thinking, bool):
            raise ConfigError(prefix + "thinking 必须是布尔值。")
        if entry["protocol"] == "openai" and thinking:
            raise ConfigError(prefix + "OpenAI 本轮不支持 thinking=true。")
        names.add(entry["name"])
        result.append(
            ProviderConfig(
                **{k: v for k, v in entry.items() if k != "base_url"},
                base_url=_base_url(entry["base_url"], entry["protocol"]),
            )
        )
    return tuple(result)


def select_provider(configs: tuple[ProviderConfig, ...], name: str | None) -> ProviderConfig:
    if not configs:
        raise ConfigError("供应商列表不能为空。")
    if name is None:
        return configs[0]
    for config in configs:
        if config.name == name:
            return config
    raise ConfigError("未找到指定供应商，请检查 --provider 对应的 name。")

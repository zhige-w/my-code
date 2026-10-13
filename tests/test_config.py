from dataclasses import replace

import pytest
import yaml

from mycode.config import load_config, select_provider
from mycode.errors import ConfigError


@pytest.fixture
def config_file(tmp_path):
    def write(data):
        path = tmp_path / "mycode.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    return write


def entry(**changes):
    return dict(
        name="work",
        protocol="openai",
        model="example-model",
        base_url="https://example.test/v1",
        api_key="unique-test-secret",
        **changes,
    )


def test_defaults_and_selection(config_file):
    first = entry()
    second = {**first, "name": "claude", "protocol": "anthropic", "thinking": True}
    configs = load_config(config_file([first, second]))
    assert select_provider(configs, None) == configs[0]
    assert select_provider(configs, "claude") == configs[1]
    assert not configs[0].thinking
    assert configs[1].thinking
    assert "unique-test-secret" not in repr(configs[0])
    with pytest.raises(ConfigError):
        select_provider(configs, "missing")
    with pytest.raises(ConfigError):
        select_provider((), None)
    assert replace(configs[0], name="copy").name == "copy"


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        [None],
        [entry(), entry()],
        [{**entry(), "protocol": "unknown"}],
        [{**entry(), "thinking": True}],
        [{**entry(), "thinking": "true"}],
        [{**entry(), "extra": "unique-test-secret"}],
        [{**entry(), "model": " "}],
        [{**entry(), "api_key": None}],
        [{k: v for k, v in entry().items() if k != "model"}],
    ],
)
def test_invalid_configs(config_file, data):
    with pytest.raises(ConfigError) as error:
        load_config(config_file(data))
    assert "unique-test-secret" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test",
        "https://user:secret@example.test",
        "https://example.test?q=secret",
        "https://example.test/#secret",
        "https://example.test/v1/messages",
        "https://example.test/v1/chat/completions",
        "https://[broken",
        "https://x:invalid",
        "https://example. test",
        "https://",
    ],
)
def test_invalid_urls(config_file, url):
    with pytest.raises(ConfigError):
        load_config(config_file([{**entry(), "base_url": url}]))


def test_base_url_normalization(config_file):
    configs = load_config(
        config_file(
            [
                {**entry(), "base_url": "https://example.test/gateway/v1/"},
                {
                    **entry(),
                    "name": "a",
                    "protocol": "anthropic",
                    "base_url": "https://example.test/gateway/v1/",
                },
            ]
        )
    )
    assert configs[0].base_url == "https://example.test/gateway/v1"
    assert configs[1].base_url == "https://example.test/gateway"


@pytest.mark.parametrize(
    "text",
    [
        "- name: work\n  name: unique-test-secret\n",
        "- api_key: [unique-test-secret\n",
        "!!python/object/apply:os.system [unique-test-secret]",
        "- ? [invalid, key]\n  : value\n",
    ],
)
def test_yaml_errors_safe(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError) as error:
        load_config(path)
    assert "unique-test-secret" not in str(error.value)


def test_missing_or_invalid_encoding(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing")
    path = tmp_path / "invalid.yaml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError):
        load_config(path)

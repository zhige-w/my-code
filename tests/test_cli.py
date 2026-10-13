import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from mycode import cli
from mycode.config import load_config


def entries():
    return [
        {
            "name": "first",
            "protocol": "openai",
            "model": "model-1",
            "base_url": "https://example.test/v1",
            "api_key": "unique-test-secret",
        },
        {
            "name": "second",
            "protocol": "anthropic",
            "model": "model-2",
            "base_url": "https://example.test",
            "api_key": "another-test-secret",
        },
    ]


@pytest.mark.parametrize("args,selected", [([], "first"), (["--provider", "second"], "second")])
def test_default_file_and_selection(monkeypatch, tmp_path, args, selected):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mycode.yaml").write_text(yaml.safe_dump(entries()))
    calls = []

    class App:
        def __init__(self, conversation, name, model, **kwargs):
            calls.append((name, model))

        def run(self):
            pass

    monkeypatch.setattr(cli, "MyCodeApp", App)
    cli.main(args)
    assert calls[0][0] == selected


def test_explicit_config(monkeypatch, tmp_path):
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(entries()))
    calls = []

    class App:
        def __init__(self, conversation, name, model, **kwargs):
            calls.append(name)

        def run(self):
            pass

    monkeypatch.setattr(cli, "MyCodeApp", App)
    cli.main(["--config", str(path), "--provider", "second"])
    assert calls == ["second"]


@pytest.mark.parametrize("mode", ["missing", "bad_yaml", "unknown", "thinking"])
def test_error_before_app(monkeypatch, tmp_path, capsys, mode):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "mycode.yaml"
    args = []
    if mode == "bad_yaml":
        path.write_text("- api_key: [unique-test-secret")
    elif mode in {"unknown", "thinking"}:
        data = entries()
        if mode == "thinking":
            data[0]["thinking"] = True
        path.write_text(yaml.safe_dump(data))
        if mode == "unknown":
            args = ["--provider", "unknown"]

    def unexpected(*args, **kwargs):
        pytest.fail("无效配置不应创建客户端或启动界面")

    monkeypatch.setattr(cli, "create_provider", unexpected)
    monkeypatch.setattr(cli, "MyCodeApp", unexpected)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "配置错误" in output
    assert "unique-test-secret" not in output and "Traceback" not in output


def test_runtime_error_is_safe(monkeypatch, tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(entries()))

    class App:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            raise RuntimeError("unique-test-secret raw response")

    monkeypatch.setattr(cli, "MyCodeApp", App)
    with pytest.raises(SystemExit) as error:
        cli.main(["--config", str(path)])
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "unique-test-secret" not in output and "raw response" not in output


def test_help_without_config_or_api(tmp_path):
    for command in (
        [sys.executable, "-m", "mycode"],
        [str(Path(sys.executable).parent / "MyCode")],
    ):
        result = subprocess.run([*command, "--help"], cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0
        assert "--config" in result.stdout and "--provider" in result.stdout


def test_example_config_parses():
    configs = load_config(Path(__file__).parents[1] / "mycode.example.yaml")
    assert {c.protocol for c in configs} == {"anthropic", "openai"}
    assert all(c.api_key.startswith("YOUR_") for c in configs)


def test_tools_start_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mycode.yaml").write_text(yaml.safe_dump(entries()))

    class App:
        def __init__(self, conversation, *args, **kwargs):
            assert conversation._executor.context.working_directory == tmp_path
            assert len(conversation._executor.registry.definitions()) == 6

        def run(self):
            pass

    monkeypatch.setattr(cli, "MyCodeApp", App)
    cli.main([])

"""验证本次 wheel 在隔离安装及项目外目录中的工作进程。"""

import json
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    wheel = ROOT / "dist/mycode-0.1.0-py3-none-any.whl"
    report = {"wheel": str(wheel), "checks": {}}
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "mycode/tools/worker.py" in names and "mycode/tui/app.tcss" in names
        metadata = archive.read("mycode-0.1.0.dist-info/METADATA").decode()
        assert "Requires-Dist: jsonschema" in metadata
        report["checks"]["wheel_contents"] = "通过"
    with tempfile.TemporaryDirectory(prefix="mycode-wheel-") as directory:
        base = Path(directory)
        environment = base / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / "bin/python"
        work = base / "outside"
        work.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONNOUSERSITE"] = "1"

        def run(args, **kwargs):
            result = subprocess.run(
                args, cwd=work, env=env, text=True, capture_output=True, timeout=180, **kwargs
            )
            if result.returncode:
                raise RuntimeError(f"隔离安装验证失败，退出码 {result.returncode}")
            return result.stdout

        run([str(python), "-m", "pip", "install", str(wheel)])
        report["checks"]["isolated_install"] = "通过"
        assert "--config" in run([str(environment / "bin/MyCode"), "--help"])
        report["checks"]["outside_cli"] = "通过"
        for name, arguments in (
            ("write_file", {"path": "a", "content": "中文\r\n"}),
            ("read_file", {"path": "a"}),
        ):
            payload = {"name": name, "arguments": arguments, "working_directory": str(work)}
            output = run(
                [str(python), "-m", "mycode.tools.worker"],
                input=json.dumps(payload, ensure_ascii=False) + "\n",
            )
            frames = [json.loads(line) for line in output.splitlines()]
            final = frames[-1]
            assert final["type"] == "result" and not final.get("error")
            if name == "read_file":
                assert final["data"]["content"] == "1: 中文\r\n"
        assert (work / "a").read_bytes() == "中文\r\n".encode()
        report["checks"]["outside_worker_read_write"] = "通过"
    target = ROOT / "specs/tool-system/evidence/packaging.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

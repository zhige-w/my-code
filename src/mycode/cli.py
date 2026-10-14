"""MyCode 命令行入口。"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from mycode.agent.options import AgentOptions
from mycode.config import load_config, select_provider
from mycode.conversation import Conversation
from mycode.errors import ConfigError, SecretRedactor, configure_logging
from mycode.providers import create_provider
from mycode.tools import create_tool_registry
from mycode.tools.base import ToolContext
from mycode.tools.executor import ToolExecutor
from mycode.tui.app import MyCodeApp


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="MyCode", description="全屏终端 AI 对话助手")
    parser.add_argument("--config", type=Path, default=Path("mycode.yaml"), help="YAML 配置路径")
    parser.add_argument("--provider", help="供应商 name，默认使用配置列表第一项")
    args = parser.parse_args(argv)
    conversation = None
    try:
        configs = load_config(args.config)
        redactor = SecretRedactor(config.api_key for config in configs)
        configure_logging(redactor)
        config = select_provider(configs, args.provider)
        executor = ToolExecutor(create_tool_registry(), ToolContext(Path.cwd()), redactor)
        conversation = Conversation(create_provider(config), executor, options=AgentOptions())
        MyCodeApp(conversation, config.name, config.model, redactor=redactor).run()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        pass
    except Exception:
        print("MyCode 启动或运行失败，请检查终端环境和供应商配置。", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if conversation is not None:
            with suppress(Exception):
                asyncio.run(conversation.aclose())

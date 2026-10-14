"""单次 Agent 运行的兜底限制。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentOptions:
    max_iterations: int = 20
    unknown_tool_batch_limit: int = 3
    max_read_concurrency: int = 4

    def __post_init__(self):
        for value in vars(self).values():
            if type(value) is not int or value <= 0:
                raise ValueError("Agent 限制必须为正整数。")

"""模式提示与内存计划。"""

from dataclasses import dataclass
from typing import Literal

from mycode.models import Message

AgentMode = Literal["plan", "execute"]


@dataclass(frozen=True)
class PlanSnapshot:
    request: Message
    reply: Message


def build_mode_prompt(mode: AgentMode) -> str:
    common = "你是 MyCode 终端编程助手。使用工具结果调整行动，完成后明确说明结果。"
    if mode == "plan":
        return common + (
            "当前为规划模式：只能读取和搜索，禁止修改或执行命令。"
            "调查后输出具体可执行计划，等待用户 /do。"
        )
    return common + "当前为执行模式：自主完成用户任务，必要时连续调用工具验证结果，无需用户催促。"

"""MyCode 核心工具。"""


def create_tool_registry():
    from mycode.tools.command import ExecuteCommandTool
    from mycode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
    from mycode.tools.registry import ToolRegistry
    from mycode.tools.search import FindFilesTool, SearchCodeTool

    registry = ToolRegistry()
    for kind in (
        ReadFileTool,
        WriteFileTool,
        EditFileTool,
        ExecuteCommandTool,
        FindFilesTool,
        SearchCodeTool,
    ):
        registry.register(kind())
    return registry

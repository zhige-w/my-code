"""安全错误提示和密钥保护。"""

import logging
from collections.abc import Iterable


class ConfigError(Exception):
    """配置错误只包含可安全展示的提示。"""


class ProviderError(Exception):
    def __init__(self, kind: str, user_message: str) -> None:
        self.kind = kind
        self.user_message = user_message
        super().__init__(user_message)


class SecretRedactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))

    def __repr__(self) -> str:
        return "SecretRedactor(<已隐藏>)"

    def redact(self, text: str, *, partial: bool = False) -> str:
        # 增量末尾可能只是密钥的前缀，先不显示，避免跨片段泄露。
        if partial:
            pending = 0
            for secret in self._secrets:
                for size in range(1, min(len(secret), len(text) + 1)):
                    if text.endswith(secret[:size]):
                        pending = max(pending, size)
            if pending:
                text = text[:-pending]
        for secret in self._secrets:
            text = text.replace(secret, "[密钥已隐藏]")
        return text


class _SecretFilter(logging.Filter):
    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        # SDK 日志可能携带响应体，统一抑制其输出而不改变宿主日志。
        if record.name.split(".")[0] in {"openai", "anthropic", "httpx", "httpcore"}:
            return False
        record.msg = self.redactor.redact(record.getMessage())
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def configure_logging(redactor: SecretRedactor) -> None:
    for name in ("openai", "anthropic", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.disabled = True
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(_SecretFilter(redactor))


def map_provider_error(exc: Exception) -> ProviderError:
    # 只按异常类型和状态码分类，不将供应商返回的原始文本交给界面。
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return ProviderError("authentication", "认证失败或无访问权限，请检查 API key 和模型权限。")
    if status == 429:
        return ProviderError("rate_limit", "请求受限，请稍后重试。")
    if "Timeout" in name:
        return ProviderError("timeout", "请求超时，请检查网络后重新提问。")
    if "Connection" in name or "Connect" in name or "ReadError" in name:
        return ProviderError("connection", "网络连接异常，请检查网络后重新提问。")
    if isinstance(status, int):
        if status == 400:
            # 仅识别明确的模式错误；原文从不对外输出。
            body = str(getattr(exc, "body", ""))
            if "thinking" in body and any(
                marker in body for marker in ("not supported", "unsupported", "不支持")
            ):
                return ProviderError("response", "该模型不支持当前手动 thinking 模式。")
        return ProviderError("response", f"模型服务请求失败（HTTP {status}），请检查配置后重试。")
    return ProviderError("protocol", "模型响应格式异常或连接中断，请重新提问。")

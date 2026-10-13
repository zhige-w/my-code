"""语言模型协议适配器。"""

from mycode.config import ProviderConfig
from mycode.errors import ConfigError
from mycode.providers.anthropic import AnthropicProvider
from mycode.providers.base import Provider
from mycode.providers.openai import OpenAIProvider


def create_provider(config: ProviderConfig) -> Provider:
    if config.protocol == "anthropic":
        return AnthropicProvider(config)
    if config.protocol == "openai":
        if config.thinking:
            raise ConfigError("OpenAI 本轮不支持 thinking=true。")
        return OpenAIProvider(config)
    raise ConfigError("不支持该供应商协议。")

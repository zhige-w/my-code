import logging
from dataclasses import replace

import pytest

from mycode.errors import (
    ConfigError,
    SecretRedactor,
    configure_logging,
    map_provider_error,
)
from mycode.providers import create_provider
from mycode.providers.anthropic import AnthropicProvider
from mycode.providers.base import REQUEST_TIMEOUT
from mycode.providers.openai import OpenAIProvider


async def test_factory_and_timeout(config):
    for protocol, kind in (("openai", OpenAIProvider), ("anthropic", AnthropicProvider)):
        provider = create_provider(replace(config, protocol=protocol))
        assert isinstance(provider, kind)
        assert provider._client.max_retries == 0
        timeout = provider._client.timeout
        assert timeout == REQUEST_TIMEOUT
        assert timeout.connect == timeout.write == timeout.pool == 10
        assert timeout.read == 120
        await provider.aclose()
    with pytest.raises(ConfigError):
        create_provider(replace(config, thinking=True))
    with pytest.raises(ConfigError):
        create_provider(replace(config, protocol="other"))


def test_redaction_and_partial_keys():
    redactor = SecretRedactor(["unique-test-secret", "another-key"])
    assert "unique-test-secret" not in repr(redactor)
    assert redactor.redact("hello unique-test-secret") == "hello [密钥已隐藏]"
    assert redactor.redact("hello unique-test-", partial=True) == "hello "
    assert redactor.redact("hello unique-test-", partial=False) == "hello unique-test-"
    assert redactor.redact("another-key unique-test-secret") == "[密钥已隐藏] [密钥已隐藏]"


def test_safe_errors_and_logs(caplog):
    secret = "unique-test-secret"
    assert secret not in str(map_provider_error(RuntimeError(secret)))
    logger = logging.getLogger("mycode.test")
    configure_logging(SecretRedactor([secret]))
    with caplog.at_level(logging.DEBUG):
        logger.warning("problem %s", secret)
        logging.getLogger("openai").error("response body %s", secret)
        logging.getLogger("anthropic._client").error("response body %s", secret)
    assert secret not in caplog.text
    assert "response body" not in caplog.text
    assert "[密钥已隐藏]" in caplog.text

"""Azure AI Foundry client wrapper (*.services.ai.azure.com serverless API)."""

import time
from urllib.parse import urlparse
import httpx
from openai import OpenAI, RateLimitError, APIError
from loguru import logger
import config

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Return a singleton OpenAI client pointed at the AI Foundry hub endpoint.

    Azure AI Foundry stores the full inference URL in AZURE_AI_ENDPOINT
    (e.g. https://hub.services.ai.azure.com/models/chat/completions?api-version=...).
    We extract only scheme+host and rebuild the base_url as /models so the
    OpenAI SDK can append /chat/completions correctly.
    """
    global _client
    if _client is None:
        parsed = urlparse(config.AZURE_AI_ENDPOINT)
        base_url = f"{parsed.scheme}://{parsed.netloc}/models"
        _client = OpenAI(
            api_key=config.AZURE_AI_API_KEY,
            base_url=base_url,
            default_query={"api-version": config.AZURE_AI_API_VERSION},
            http_client=httpx.Client(verify=not config.SSL_NO_VERIFY),
        )
    return _client


def _chat(messages: list[dict[str, str]], max_retries: int = 3) -> str:
    """Send a chat request with exponential backoff on rate limit errors.

    Args:
        messages: OpenAI-format message list.
        max_retries: Maximum retry attempts before raising.

    Returns:
        Assistant reply as a plain string.
    """
    client = _get_client()
    delays = [2, 4, 8]

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=config.AZURE_AI_DEPLOYMENT,
                messages=messages,  # type: ignore[arg-type]
            )
            tokens = response.usage.total_tokens if response.usage else "?"
            reply = response.choices[0].message.content or ""
            logger.info(f"[LLM] deployment={config.AZURE_AI_DEPLOYMENT} tokens_used={tokens}")
            return reply

        except RateLimitError:
            if attempt == max_retries - 1:
                raise
            delay = delays[attempt]
            logger.warning(f"[LLM] Rate limited. Retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)

        except APIError as exc:
            logger.error(f"[LLM] API error on attempt {attempt + 1}: {exc}")
            if attempt == max_retries - 1:
                raise
            time.sleep(delays[attempt])

    return ""


def chat_short(messages: list[dict[str, str]]) -> str:
    """Send a short-context chat request (scoring, summarising).

    Args:
        messages: OpenAI-format message list.

    Returns:
        Assistant reply string.
    """
    return _chat(messages)


def chat_long(messages: list[dict[str, str]]) -> str:
    """Send a long-context chat request (resume + job description together).

    Args:
        messages: OpenAI-format message list.

    Returns:
        Assistant reply string.
    """
    return _chat(messages)


if __name__ == "__main__":
    config.validate()
    reply = chat_short([{"role": "user", "content": "Say hello in one sentence."}])
    logger.info(f"[LLM] Test reply: {reply}")

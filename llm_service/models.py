"""Chat-model factories for the Ollama and Gemini backends.

Model creation is isolated here so the agent layer depends only on a small,
uniform "something with an ``.invoke`` method" contract and never on a specific
provider SDK. Both factories degrade gracefully: a missing optional dependency
logs a warning and leaves the caller on the remaining backend rather than
crashing the service at import time.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from llm_service.config import parse_float_env, parse_int_env

LOGGER = logging.getLogger(__name__)

# Small, fast instruct model that fits in RAM on a CPU-only host: good English
# adherence with low latency. Bump to 7b/14b on machines with more memory or a GPU.
DEFAULT_MODEL = "ollama:qwen2.5:3b-instruct"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
# Cap context and output to bound memory use and latency; the grounded prompt is small.
DEFAULT_OLLAMA_NUM_CTX = 4096
DEFAULT_OLLAMA_NUM_PREDICT = 512
DEFAULT_OLLAMA_KEEP_ALIVE = "30m"
DEFAULT_LLM_TEMPERATURE = 0.1
DEFAULT_GEMINI_TIMEOUT_SECONDS = 10.0


def create_chat_model(model_name: str) -> Any:
    """Create the configured chat model, keeping Ollama models warm when possible."""
    if not model_name.startswith("ollama:"):
        return model_name
    try:
        from langchain_ollama import ChatOllama
    except Exception as error:
        LOGGER.warning("langchain-ollama is unavailable; falling back to model string: %s", error)
        return model_name

    base_url = os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST")
    model_kwargs: dict[str, Any] = {
        "model": model_name.removeprefix("ollama:"),
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", DEFAULT_OLLAMA_KEEP_ALIVE),
        "temperature": parse_float_env("LLM_TEMPERATURE", DEFAULT_LLM_TEMPERATURE),
        "num_ctx": parse_int_env("OLLAMA_NUM_CTX", DEFAULT_OLLAMA_NUM_CTX),
        "num_predict": parse_int_env("OLLAMA_NUM_PREDICT", DEFAULT_OLLAMA_NUM_PREDICT),
    }
    if base_url:
        model_kwargs["base_url"] = base_url
    return ChatOllama(**model_kwargs)


def create_gemini_chat_model(api_key: str) -> Any | None:
    """Create the optional Gemini client; a missing integration leaves Ollama active."""
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except Exception as error:
        LOGGER.warning(
            "GEMINI_API_KEY is set but langchain-google-genai is unavailable; using Ollama: %s",
            error,
        )
        return None
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        api_key=api_key,
        temperature=parse_float_env("LLM_TEMPERATURE", DEFAULT_LLM_TEMPERATURE),
        timeout=parse_float_env("GEMINI_TIMEOUT_SECONDS", DEFAULT_GEMINI_TIMEOUT_SECONDS),
        max_retries=0,
    )

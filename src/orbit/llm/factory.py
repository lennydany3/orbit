"""Picks the language model: Groq (cloud, fast) when its key is set, otherwise the local Ollama model."""
from .. import config
from .base import LLM, LLMError


def resolve_provider(provider: str | None = None) -> str:
    """"groq" or "ollama". "auto" means Groq if GROQ_API_KEY is set. Asking for groq without a key is an error, not a silent downgrade."""
    from .groq_llm import config_key

    provider = (provider or config.LLM_PROVIDER or "auto").lower()
    if provider == "auto":
        return "groq" if config_key() else "ollama"
    if provider not in ("groq", "ollama"):
        raise LLMError(f"Unknown language-model provider '{provider}'. Use auto, groq or ollama.")
    return provider


def make_llm(provider: str | None = None, ollama_model: str = config.MODEL) -> tuple[LLM, str]:
    """Returns (llm, description) where the description is for the start-up banner."""
    chosen = resolve_provider(provider)
    if chosen == "groq":
        from .groq_llm import GroqLLM

        return GroqLLM(), f"Groq {config.GROQ_MODEL} (cloud)"
    from .ollama_llm import OllamaLLM

    return OllamaLLM(model=ollama_model), f"Ollama {ollama_model} (local)"

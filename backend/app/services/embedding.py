import math
from collections.abc import Sequence

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from app.core.config import get_settings


class EmbeddingConfigurationError(RuntimeError):
    pass


class EmbeddingProviderError(RuntimeError):
    pass


class EmbeddingResponseError(RuntimeError):
    pass


PROVIDER_ERRORS = (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)


def _validated_texts(texts: Sequence[str]) -> list[str]:
    if isinstance(texts, str) or not texts:
        raise ValueError("texts must be a non-empty sequence")
    normalized = [text.strip() for text in texts]
    if any(not text for text in normalized):
        raise ValueError("embedding text must not be blank")
    return normalized


def embed_texts_with_client(
    client: OpenAI,
    model: str,
    dimensions: int,
    texts: Sequence[str],
) -> list[list[float]]:
    normalized = _validated_texts(texts)
    response = client.embeddings.create(
        model=model,
        input=normalized,
        dimensions=dimensions,
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    if len(ordered) != len(normalized) or [item.index for item in ordered] != list(
        range(len(normalized))
    ):
        raise EmbeddingResponseError("Embedding provider returned incomplete data")

    vectors: list[list[float]] = []
    for item in ordered:
        vector = [float(value) for value in item.embedding]
        if len(vector) != dimensions:
            raise EmbeddingResponseError(
                f"Embedding provider returned {len(vector)} dimensions; expected {dimensions}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise EmbeddingResponseError("Embedding provider returned a non-finite value")
        vectors.append(vector)
    return vectors


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.dashscope_api_key or not settings.dashscope_api_key.strip():
        raise EmbeddingConfigurationError("Embedding service is not configured")
    if not settings.dashscope_base_url or not settings.dashscope_base_url.strip():
        raise EmbeddingConfigurationError("Embedding service is not configured")

    client = OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        timeout=30.0,
        max_retries=1,
    )
    try:
        return embed_texts_with_client(
            client=client,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            texts=texts,
        )
    except EmbeddingResponseError:
        raise
    except PROVIDER_ERRORS as exc:
        raise EmbeddingProviderError(
            "Embedding service is temporarily unavailable"
        ) from exc


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]

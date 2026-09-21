from dataclasses import dataclass
from time import perf_counter

from openai import OpenAI

from app.core.config import get_settings
from app.services.knowledge.prompts import (
    build_grounded_system_prompt,
    build_grounded_user_prompt,
)
from app.services.knowledge.retrieval import RetrievedChunk
from app.services.knowledge.schemas import GroundedAnswer


class KnowledgeConfigurationError(RuntimeError):
    pass


class GroundedStructuredOutputError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderUsage:
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class GroundedGeneration:
    answer: GroundedAnswer
    usage: ProviderUsage
    latency_ms: float


def _provider_usage(completion) -> ProviderUsage:
    usage = getattr(completion, "usage", None)
    return ProviderUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )


def grounded_answer_with_client(
    client: OpenAI,
    model: str,
    question: str,
    candidates: list[RetrievedChunk],
) -> GroundedGeneration:
    started = perf_counter()
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": build_grounded_system_prompt()},
            {
                "role": "user",
                "content": build_grounded_user_prompt(question, candidates),
            },
        ],
        response_format=GroundedAnswer,
    )
    latency_ms = (perf_counter() - started) * 1000
    if not completion.choices:
        raise GroundedStructuredOutputError("The model returned no choices")
    message = completion.choices[0].message
    if message.refusal:
        raise GroundedStructuredOutputError("The model refused the grounded query")
    if message.parsed is None:
        raise GroundedStructuredOutputError("The model returned no structured result")
    return GroundedGeneration(
        answer=GroundedAnswer.model_validate(message.parsed),
        usage=_provider_usage(completion),
        latency_ms=latency_ms,
    )


def generate_grounded_answer(
    question: str, candidates: list[RetrievedChunk]
) -> GroundedGeneration:
    settings = get_settings()
    required_settings = {
        "DASHSCOPE_API_KEY": settings.dashscope_api_key,
        "DASHSCOPE_BASE_URL": settings.dashscope_base_url,
        "QWEN_MODEL": settings.qwen_model,
    }
    missing = [
        name for name, value in required_settings.items() if not value or not value.strip()
    ]
    if missing:
        raise KnowledgeConfigurationError("Knowledge answer service is not configured")

    client = OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        timeout=60.0,
        max_retries=1,
    )
    return grounded_answer_with_client(
        client=client,
        model=settings.qwen_model,
        question=question,
        candidates=candidates,
    )

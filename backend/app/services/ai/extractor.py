from datetime import date

from openai import OpenAI

from app.core.config import get_settings
from app.core.observability import observe_ai_call
from app.services.ai.prompts import build_system_prompt
from app.services.ai.schemas import ExtractionResult
from app.services.ai_guard import ensure_paid_ai_enabled


class AIConfigurationError(RuntimeError):
    pass


class StructuredOutputError(RuntimeError):
    pass


def extract_with_client(
    client: OpenAI,
    model: str,
    current_date: date,
    text: str,
) -> ExtractionResult:
    ensure_paid_ai_enabled()
    if len(text) > get_settings().ai_extraction_max_input_chars:
        raise ValueError("extraction text exceeds the maximum length")
    completion = observe_ai_call(
        "structured_extraction",
        lambda: client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": build_system_prompt(current_date)},
                {"role": "user", "content": text},
            ],
            response_format=ExtractionResult,
            max_tokens=get_settings().ai_qwen_max_output_tokens,
        ),
    )
    if not completion.choices:
        raise StructuredOutputError("The model returned no choices")
    message = completion.choices[0].message
    if message.refusal:
        raise StructuredOutputError("The model refused the extraction")
    if message.parsed is None:
        raise StructuredOutputError("The model returned no structured result")
    return ExtractionResult.model_validate(message.parsed)


def require_extraction_configuration() -> None:
    settings = get_settings()
    required_settings = {
        "DASHSCOPE_API_KEY": settings.dashscope_api_key,
        "DASHSCOPE_BASE_URL": settings.dashscope_base_url,
        "QWEN_MODEL": settings.qwen_model,
    }
    missing = [name for name, value in required_settings.items() if not value or not value.strip()]
    if missing:
        raise AIConfigurationError(
            f"Missing required AI configuration: {', '.join(missing)}"
        )


def extract_course_content(text: str, current_date: date | None = None) -> ExtractionResult:
    ensure_paid_ai_enabled()
    require_extraction_configuration()
    settings = get_settings()
    if len(text) > settings.ai_extraction_max_input_chars:
        raise ValueError("extraction text exceeds the maximum length")

    client = OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        timeout=60.0,
        max_retries=0,
    )
    return extract_with_client(
        client=client,
        model=settings.qwen_model,
        current_date=current_date or date.today(),
        text=text,
    )

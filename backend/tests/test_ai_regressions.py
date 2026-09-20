from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)
from pydantic import ValidationError

from app.services.ai.extractor import StructuredOutputError, extract_with_client
from app.services.ai.schemas import ExtractionResult


def confirmed_payload() -> dict:
    return {
        "course": {"mode": "CREATE_NEW", "name": "Confirmed course"},
        "draft": {
            "course_name": "Extracted course suggestion",
            "title": "User reviewed task",
            "deadline": None,
            "priority": "MEDIUM",
            "tasks": [
                {
                    "title": "Complete the assignment",
                    "description": None,
                    "deadline": None,
                    "priority": "MEDIUM",
                }
            ],
        },
    }


def test_import_requires_authentication(client: TestClient):
    response = client.post("/api/v1/ai/import", json=confirmed_payload())

    assert response.status_code == 401


def test_confirm_import_does_not_call_qwen(
    client: TestClient, auth_headers: dict[str, str], monkeypatch
):
    def unexpected_extraction(*args, **kwargs):
        raise AssertionError("Confirmation must never call the AI provider")

    monkeypatch.setattr(
        "app.api.routes.ai.extract_course_content", unexpected_extraction
    )
    monkeypatch.setattr(
        "app.services.ai.extractor.extract_with_client", unexpected_extraction
    )

    response = client.post(
        "/api/v1/ai/import", headers=auth_headers, json=confirmed_payload()
    )

    assert response.status_code == 201
    assert response.json()["task"]["source"] == "AI"
    assert response.json()["task"]["title"] == "User reviewed task"
    assert response.json()["course"]["name"] == "Confirmed course"


def test_import_keeps_required_step_with_attached_optional_detail(
    client: TestClient, auth_headers: dict[str, str]
):
    payload = confirmed_payload()
    payload["draft"]["title"] = "软件工程作业"
    payload["draft"]["tasks"] = [
        {
            "title": "需求分析",
            "description": "可选事项：整理参考资料",
            "deadline": None,
            "priority": "MEDIUM",
        },
        {
            "title": "绘制ER图",
            "description": None,
            "deadline": None,
            "priority": "MEDIUM",
        },
    ]

    response = client.post("/api/v1/ai/import", headers=auth_headers, json=payload)

    assert response.status_code == 201
    assert response.json()["task"]["description"] == (
        "完成要求：\n- 需求分析\n- 绘制ER图\n\n可选事项：\n- 整理参考资料"
    )


def test_import_single_required_step_keeps_optional_detail(
    client: TestClient, auth_headers: dict[str, str]
):
    payload = confirmed_payload()
    payload["draft"]["title"] = "完成数据库实验"
    payload["draft"]["tasks"][0].update(
        {"title": "完成数据库实验", "description": "可选事项：整理实验报告"}
    )

    response = client.post("/api/v1/ai/import", headers=auth_headers, json=payload)

    assert response.status_code == 201
    assert response.json()["task"]["title"] == "完成数据库实验"
    assert response.json()["task"]["description"] == "可选事项：\n- 整理实验报告"
    assert len(client.get("/api/v1/tasks", headers=auth_headers).json()) == 1


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_detail"),
    [
        ("connection", 503, "AI extraction service is temporarily unavailable"),
        ("timeout", 503, "AI extraction service is temporarily unavailable"),
        ("rate_limit", 503, "AI extraction service is temporarily unavailable"),
        ("server", 502, "AI provider request failed"),
        ("authentication", 502, "AI provider request failed"),
    ],
)
def test_provider_failures_return_safe_errors_without_writes(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
    failure: str,
    expected_status: int,
    expected_detail: str,
):
    private_details = "provider-internal-diagnostic-must-not-be-returned"
    request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
    errors = {
        "connection": APIConnectionError(message=private_details, request=request),
        "timeout": APITimeoutError(request=request),
        "rate_limit": RateLimitError(
            private_details,
            response=httpx.Response(429, request=request),
            body={"detail": private_details},
        ),
        "server": InternalServerError(
            private_details,
            response=httpx.Response(500, request=request),
            body={"detail": private_details},
        ),
        "authentication": AuthenticationError(
            private_details,
            response=httpx.Response(401, request=request),
            body={"detail": private_details},
        ),
    }

    def fail_extract(_: str):
        raise errors[failure]

    monkeypatch.setattr("app.api.routes.ai.extract_course_content", fail_extract)

    response = client.post(
        "/api/v1/ai/extract", headers=auth_headers, json={"text": "完成课程作业。"}
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert private_details not in response.text
    assert "provider.invalid" not in response.text
    assert client.get("/api/v1/courses", headers=auth_headers).json() == []
    assert client.get("/api/v1/tasks", headers=auth_headers).json() == []


@pytest.mark.parametrize(
    ("completion", "message"),
    [
        (SimpleNamespace(choices=[]), "no choices"),
        (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(refusal="refused", parsed=None))]
            ),
            "refused",
        ),
        (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, parsed=None))]
            ),
            "no structured result",
        ),
    ],
)
def test_extractor_rejects_missing_or_refused_structured_output(completion, message: str):
    provider = Mock()
    provider.chat.completions.parse.return_value = completion

    with pytest.raises(StructuredOutputError, match=message):
        extract_with_client(provider, "test-model", date(2026, 9, 19), "完成课程作业。")

    assert provider.chat.completions.parse.call_args.kwargs["response_format"] is ExtractionResult


def test_extractor_revalidates_provider_structured_result():
    provider = Mock()
    invalid_draft = confirmed_payload()["draft"]
    invalid_draft["priority"] = "URGENT"
    provider.chat.completions.parse.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, parsed=invalid_draft))]
    )

    with pytest.raises(ValidationError):
        extract_with_client(provider, "test-model", date(2026, 9, 19), "完成课程作业。")

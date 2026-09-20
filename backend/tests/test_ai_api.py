from copy import deepcopy
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.task import Task
from app.services.ai.extractor import AIConfigurationError, StructuredOutputError
from app.services.ai.prompts import build_system_prompt
from app.services.ai.schemas import ExtractionResult, Priority, TaskDraft


def test_extract_requires_authentication(client: TestClient):
    response = client.post("/api/v1/ai/extract", json={"text": "完成数据库实验。"})

    assert response.status_code == 401


def test_extract_returns_structured_draft_without_database_writes(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    captured: dict[str, str] = {}

    def fake_extract(text: str) -> ExtractionResult:
        captured["text"] = text
        return ExtractionResult(
            course_name="数据库",
            title="数据库实验",
            deadline=date(2026, 9, 20),
            priority=Priority.MEDIUM,
            tasks=[
                TaskDraft(
                    title="完成数据库实验",
                    description="可选事项：整理实验报告",
                    deadline=date(2026, 9, 20),
                    priority=Priority.MEDIUM,
                )
            ],
        )

    monkeypatch.setattr("app.api.routes.ai.extract_course_content", fake_extract)

    response = client.post(
        "/api/v1/ai/extract",
        headers=auth_headers,
        json={"text": "  数据库实验本周完成，有时间的话把实验报告也整理一下。  "},
    )

    assert response.status_code == 200
    assert captured["text"] == "数据库实验本周完成，有时间的话把实验报告也整理一下。"
    assert response.json() == {
        "course_name": "数据库",
        "title": "数据库实验",
        "deadline": "2026-09-20",
        "priority": "MEDIUM",
        "tasks": [
            {
                "title": "完成数据库实验",
                "description": "可选事项：整理实验报告",
                "deadline": "2026-09-20",
                "priority": "MEDIUM",
            }
        ],
    }
    assert client.get("/api/v1/courses", headers=auth_headers).json() == []
    assert client.get("/api/v1/tasks", headers=auth_headers).json() == []


def test_extract_rejects_blank_text_and_extra_fields(
    client: TestClient,
    auth_headers: dict[str, str],
):
    blank = client.post(
        "/api/v1/ai/extract", headers=auth_headers, json={"text": "   "}
    )
    extra = client.post(
        "/api/v1/ai/extract",
        headers=auth_headers,
        json={"text": "完成作业", "confirm": True},
    )

    assert blank.status_code == 422
    assert extra.status_code == 422


def test_extract_hides_configuration_details(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    def fail_extract(_: str) -> ExtractionResult:
        raise AIConfigurationError("Missing required AI configuration: DASHSCOPE_API_KEY")

    monkeypatch.setattr("app.api.routes.ai.extract_course_content", fail_extract)

    response = client.post(
        "/api/v1/ai/extract",
        headers=auth_headers,
        json={"text": "完成数据库实验。"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "AI extraction service is not configured"}
    assert "DASHSCOPE_API_KEY" not in response.text


def test_extract_rejects_invalid_provider_output(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
):
    def fail_extract(_: str) -> ExtractionResult:
        raise StructuredOutputError("provider response contained internal details")

    monkeypatch.setattr("app.api.routes.ai.extract_course_content", fail_extract)

    response = client.post(
        "/api/v1/ai/extract",
        headers=auth_headers,
        json={"text": "完成数据库实验。"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "AI provider returned an invalid structured result"
    }
    assert "internal details" not in response.text


def test_prompt_preserves_weak_intent_without_promoting_it():
    prompt = build_system_prompt(date(2026, 9, 17))

    assert "“明天”对应 2026-09-18" in prompt
    assert "不得创建新的平级正式任务" in prompt
    assert "不得让它单独占用 tasks 数组中的一个元素" in prompt
    assert "必须保留该信息" in prompt
    assert "可选事项" in prompt
    assert "tasks 就不得为空" in prompt
    assert "不得重复写入 description" in prompt


def import_payload(course: dict) -> dict:
    return {
        "course": course,
        "draft": {
            "course_name": "软件工程",
            "title": "软件工程作业",
            "deadline": "2026-09-20",
            "priority": "MEDIUM",
            "tasks": [
                {"title": "完成需求分析", "description": None, "deadline": None, "priority": "MEDIUM"},
                {"title": "绘制ER图", "description": None, "deadline": None, "priority": "MEDIUM"},
                {"title": "绘制状态图", "description": None, "deadline": None, "priority": "MEDIUM"},
            ],
        },
    }


def test_import_uses_owned_course_and_creates_one_ai_task(
    client: TestClient,
    auth_headers: dict[str, str],
):
    course = client.post(
        "/api/v1/courses", headers=auth_headers, json={"name": "Software Engineering"}
    ).json()

    response = client.post(
        "/api/v1/ai/import",
        headers=auth_headers,
        json=import_payload({"mode": "EXISTING", "course_id": course["id"]}),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["course_created"] is False
    assert data["course"]["id"] == course["id"]
    assert data["task"]["title"] == "软件工程作业"
    assert data["task"]["source"] == "AI"
    assert data["task"]["description"] == "完成要求：\n- 完成需求分析\n- 绘制ER图\n- 绘制状态图"
    assert len(client.get("/api/v1/courses", headers=auth_headers).json()) == 1
    assert len(client.get("/api/v1/tasks", headers=auth_headers).json()) == 1


def test_import_creates_confirmed_course_and_preserves_weak_intent(
    client: TestClient,
    auth_headers: dict[str, str],
):
    payload = import_payload({"mode": "CREATE_NEW", "name": "Database Systems"})
    payload["draft"].update(
        {
            "course_name": "数据库",
            "title": "完成数据库实验",
            "deadline": None,
            "tasks": [
                {
                    "title": "实施数据库实验",
                    "description": "完成核心步骤",
                    "deadline": None,
                    "priority": "MEDIUM",
                },
                {
                    "title": "整理实验报告",
                    "description": "可选事项：整理实验报告",
                    "deadline": None,
                    "priority": "MEDIUM",
                }
            ],
        }
    )

    response = client.post(
        "/api/v1/ai/import", headers=auth_headers, json=payload
    )

    assert response.status_code == 201
    data = response.json()
    assert data["course_created"] is True
    assert data["course"]["name"] == "Database Systems"
    assert data["task"]["description"] == (
        "完成要求：\n- 实施数据库实验：完成核心步骤\n\n"
        "可选事项：\n- 整理实验报告"
    )
    assert data["task"]["description"].count("整理实验报告") == 1
    assert data["task"]["course_id"] == data["course"]["id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("priority", "URGENT"), ("deadline", "not-a-date"), ("title", "   ")],
)
def test_import_revalidates_invalid_draft_fields(
    client: TestClient,
    auth_headers: dict[str, str],
    field: str,
    value: str,
):
    payload = deepcopy(import_payload({"mode": "CREATE_NEW", "name": "Rejected Course"}))
    payload["draft"][field] = value

    response = client.post(
        "/api/v1/ai/import", headers=auth_headers, json=payload
    )

    assert response.status_code == 422
    assert client.get("/api/v1/courses", headers=auth_headers).json() == []
    assert client.get("/api/v1/tasks", headers=auth_headers).json() == []


def test_import_rejects_another_users_course(client: TestClient):
    first = client.post(
        "/api/v1/auth/register",
        json={"email": "import-owner@example.com", "username": "Owner", "password": "Password123!"},
    ).json()
    second = client.post(
        "/api/v1/auth/register",
        json={"email": "import-intruder@example.com", "username": "Intruder", "password": "Password123!"},
    ).json()
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    course = client.post(
        "/api/v1/courses", headers=first_headers, json={"name": "Owner Course"}
    ).json()

    response = client.post(
        "/api/v1/ai/import",
        headers=second_headers,
        json=import_payload({"mode": "EXISTING", "course_id": course["id"]}),
    )

    assert response.status_code == 404
    assert client.get("/api/v1/tasks", headers=second_headers).json() == []


def test_import_rolls_back_new_course_when_task_insert_fails(
    client: TestClient,
    auth_headers: dict[str, str],
    db: Session,
    monkeypatch,
):
    original_flush = Session.flush

    def fail_task_flush(session: Session, *args, **kwargs):
        if any(isinstance(item, Task) for item in session.new):
            raise RuntimeError("forced task insert failure")
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", fail_task_flush)
    with pytest.raises(RuntimeError, match="forced task insert failure"):
        client.post(
            "/api/v1/ai/import",
            headers=auth_headers,
            json=import_payload({"mode": "CREATE_NEW", "name": "Rollback Verification Course"}),
        )

    db.expire_all()
    assert db.scalar(
        select(Course).where(Course.name == "Rollback Verification Course")
    ) is None

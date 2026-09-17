from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def register_user(client: TestClient, email: str, username: str = "Student") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": username, "password": "Password123!"},
    )
    assert response.status_code == 201
    return response.json()


def test_register_hashes_password_and_returns_current_user(client: TestClient, db: Session):
    data = register_user(client, "new@example.com", "New Student")
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "new@example.com"

    user = db.scalar(select(User).where(User.email == "new@example.com"))
    assert user is not None
    assert user.password_hash != "Password123!"

    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["username"] == "New Student"


def test_login_and_invalid_password(client: TestClient):
    register_user(client, "login@example.com")
    success = client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "Password123!"},
    )
    assert success.status_code == 200
    assert success.json()["access_token"]

    failure = client.post(
        "/api/v1/auth/login",
        json={"email": "login@example.com", "password": "wrong"},
    )
    assert failure.status_code == 401


def test_unauthenticated_access_is_rejected(client: TestClient):
    assert client.get("/api/v1/courses").status_code == 401
    assert client.get("/api/v1/tasks").status_code == 401


def test_course_crud(client: TestClient, auth_headers: dict[str, str]):
    created = client.post(
        "/api/v1/courses",
        headers=auth_headers,
        json={
            "name": "Software Engineering",
            "code": "SE101",
            "teacher": "Dr. Lin",
            "description": "Foundation course",
            "color": "#4f46e5",
        },
    )
    assert created.status_code == 201
    course_id = created.json()["id"]

    assert client.get("/api/v1/courses", headers=auth_headers).json()[0]["id"] == course_id
    assert client.get(f"/api/v1/courses/{course_id}", headers=auth_headers).status_code == 200

    updated = client.patch(
        f"/api/v1/courses/{course_id}",
        headers=auth_headers,
        json={"teacher": "Professor Lin"},
    )
    assert updated.status_code == 200
    assert updated.json()["teacher"] == "Professor Lin"

    assert client.delete(f"/api/v1/courses/{course_id}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/v1/courses/{course_id}", headers=auth_headers).status_code == 404


def test_cross_user_course_access_is_denied(client: TestClient):
    first = register_user(client, "first@example.com")
    second = register_user(client, "second@example.com")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    course_id = client.post(
        "/api/v1/courses", headers=first_headers, json={"name": "Private course"}
    ).json()["id"]

    assert client.get(f"/api/v1/courses/{course_id}", headers=second_headers).status_code == 404
    assert client.patch(
        f"/api/v1/courses/{course_id}", headers=second_headers, json={"name": "Stolen"}
    ).status_code == 404
    assert client.delete(f"/api/v1/courses/{course_id}", headers=second_headers).status_code == 404


def test_task_crud_and_status_change(client: TestClient, auth_headers: dict[str, str]):
    course_id = client.post(
        "/api/v1/courses", headers=auth_headers, json={"name": "Algorithms"}
    ).json()["id"]
    deadline = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    created = client.post(
        "/api/v1/tasks",
        headers=auth_headers,
        json={
            "title": "Finish problem set",
            "course_id": course_id,
            "deadline": deadline,
            "priority": "HIGH",
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["source"] == "MANUAL"
    assert task["status"] == "TODO"

    task_id = task["id"]
    assert client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers).status_code == 200
    assert len(client.get("/api/v1/tasks", headers=auth_headers).json()) == 1

    updated = client.patch(
        f"/api/v1/tasks/{task_id}", headers=auth_headers, json={"priority": "MEDIUM"}
    )
    assert updated.json()["priority"] == "MEDIUM"

    completed = client.patch(
        f"/api/v1/tasks/{task_id}/status",
        headers=auth_headers,
        json={"status": "COMPLETED"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"

    assert client.delete(f"/api/v1/tasks/{task_id}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers).status_code == 404


def test_cross_user_task_access_and_course_assignment_are_denied(client: TestClient):
    first = register_user(client, "task-owner@example.com")
    second = register_user(client, "intruder@example.com")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    course_id = client.post(
        "/api/v1/courses", headers=first_headers, json={"name": "Owner course"}
    ).json()["id"]
    task_id = client.post(
        "/api/v1/tasks", headers=first_headers, json={"title": "Owner task"}
    ).json()["id"]

    assert client.get(f"/api/v1/tasks/{task_id}", headers=second_headers).status_code == 404
    assert client.patch(
        f"/api/v1/tasks/{task_id}/status",
        headers=second_headers,
        json={"status": "COMPLETED"},
    ).status_code == 404
    assert client.delete(f"/api/v1/tasks/{task_id}", headers=second_headers).status_code == 404
    assert client.post(
        "/api/v1/tasks",
        headers=second_headers,
        json={"title": "Invalid association", "course_id": course_id},
    ).status_code == 404


def test_dashboard_uses_owned_database_data(client: TestClient, auth_headers: dict[str, str]):
    client.post("/api/v1/courses", headers=auth_headers, json={"name": "Databases"})
    client.post(
        "/api/v1/tasks",
        headers=auth_headers,
        json={"title": "Today", "deadline": datetime.now(UTC).isoformat()},
    )
    response = client.get("/api/v1/dashboard", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["course_count"] == 1
    assert len(data["today_tasks"]) == 1
    assert data["recent_tasks"][0]["title"] == "Today"

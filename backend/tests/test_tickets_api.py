from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import AgentRun, ConversationMessage, Employee, Ticket, TicketStatus


def create_ticket(
    client: TestClient, request_text: str = "I need write access to the payments repository."
) -> dict:
    response = client.post(
        "/api/tickets",
        json={"employee_id": "EMP001", "request_text": request_text},
    )
    assert response.status_code == 201, response.text
    return response.json()


def assert_error(response, status_code: int) -> dict:
    assert response.status_code == status_code, response.text
    error = response.json()["error"]
    assert isinstance(error["code"], str)
    assert error["code"]
    assert isinstance(error["message"], str)
    assert error["message"]
    return error


def test_create_ticket_persists_original_message(client: TestClient) -> None:
    ticket = create_ticket(client)

    assert UUID(ticket["id"])
    assert ticket["employee_id"] == "EMP001"
    assert ticket["status"] == "OPEN"
    assert ticket["created_at"]
    assert ticket["updated_at"]
    assert ticket["resolved_at"] is None
    assert ticket["final_response"] is None

    response = client.get(f"/api/tickets/{ticket['id']}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["request_text"] == ticket["request_text"]
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][0]["content"] == ticket["request_text"]


def test_request_text_is_trimmed(client: TestClient) -> None:
    ticket = create_ticket(client, "  Please grant read access to payments.  ")
    assert ticket["request_text"] == "Please grant read access to payments."


@pytest.mark.parametrize("request_text", ["", "   ", "no", "x" * 8001])
def test_invalid_request_text_is_rejected(client: TestClient, request_text: str) -> None:
    response = client.post(
        "/api/tickets", json={"employee_id": "EMP001", "request_text": request_text}
    )
    assert_error(response, 422)


def test_unknown_employee_cannot_open_ticket(client: TestClient) -> None:
    response = client.post(
        "/api/tickets",
        json={"employee_id": "EMP-NOT-FOUND", "request_text": "Read access to payments"},
    )
    assert_error(response, 404)
    assert client.get("/api/tickets").json()["total"] == 0


def test_client_cannot_set_ticket_status(client: TestClient) -> None:
    response = client.post(
        "/api/tickets",
        json={
            "employee_id": "EMP001",
            "request_text": "Read access to payments",
            "status": "RESOLVED",
        },
    )
    assert_error(response, 422)


def test_ticket_pagination_and_filters(client: TestClient) -> None:
    tickets = [
        create_ticket(client, f"Read access to payments request {index}") for index in range(3)
    ]

    first = client.get("/api/tickets", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/tickets", params={"limit": 2, "offset": 2}).json()
    assert first["total"] == second["total"] == 3
    assert first["limit"] == 2
    assert first["offset"] == 0
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    observed = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(observed)) == 3
    assert set(observed) == {ticket["id"] for ticket in tickets}
    assert observed == [item["id"] for item in client.get("/api/tickets").json()["items"]]

    filtered = client.get("/api/tickets", params={"status": "OPEN", "employee_id": "EMP001"}).json()
    assert filtered["total"] == 3
    assert client.get("/api/tickets", params={"status": "RESOLVED"}).json()["total"] == 0
    assert client.get("/api/tickets", params={"employee_id": "EMP-NOT-FOUND"}).json()["total"] == 0


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "INVALID"}]
)
def test_invalid_pagination_and_status_are_rejected(client: TestClient, params: dict) -> None:
    assert_error(client.get("/api/tickets", params=params), 422)


def test_open_ticket_can_be_edited_and_message_stays_consistent(client: TestClient) -> None:
    ticket = create_ticket(client)
    text = "I need read access to payments."
    response = client.patch(f"/api/tickets/{ticket['id']}", json={"request_text": text})
    assert response.status_code == 200, response.text
    assert response.json()["request_text"] == text

    detail = client.get(f"/api/tickets/{ticket['id']}").json()
    assert detail["request_text"] == text
    assert detail["messages"][0]["content"] == text
    assert len(detail["messages"]) == 1


def test_patch_rejects_workflow_fields(client: TestClient) -> None:
    ticket = create_ticket(client)
    response = client.patch(
        f"/api/tickets/{ticket['id']}",
        json={"request_text": "Read access to payments", "status": "RESOLVED"},
    )
    assert_error(response, 422)
    assert client.get(f"/api/tickets/{ticket['id']}").json()["status"] == "OPEN"


def test_open_ticket_can_be_deleted(client: TestClient) -> None:
    ticket = create_ticket(client)
    response = client.delete(f"/api/tickets/{ticket['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert_error(client.get(f"/api/tickets/{ticket['id']}"), 404)
    assert client.get("/api/tickets").json()["total"] == 0


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_unknown_ticket_returns_not_found(client: TestClient, method: str) -> None:
    kwargs = {"json": {"request_text": "Read access to payments"}} if method == "patch" else {}
    response = getattr(client, method)(f"/api/tickets/{uuid4()}", **kwargs)
    assert_error(response, 404)


def test_malformed_ticket_id_is_rejected(client: TestClient) -> None:
    assert_error(client.get("/api/tickets/not-a-uuid"), 422)


def test_inactive_employee_cannot_open_ticket(client: TestClient, db_session: Session) -> None:
    employee = db_session.get(Employee, "EMP001")
    assert employee is not None
    employee.is_active = False
    db_session.commit()

    response = client.post(
        "/api/tickets",
        json={"employee_id": "EMP001", "request_text": "Read access to payments"},
    )
    assert_error(response, 409)
    assert client.get("/api/tickets").json()["total"] == 0


@pytest.mark.parametrize(
    "status", [status for status in TicketStatus if status != TicketStatus.OPEN]
)
@pytest.mark.parametrize("method", ["patch", "delete"])
def test_workflow_tickets_are_immutable(
    client: TestClient, db_session: Session, status: TicketStatus, method: str
) -> None:
    created = create_ticket(client)
    ticket = db_session.get(Ticket, UUID(created["id"]))
    assert ticket is not None
    ticket.status = status
    if status == TicketStatus.RESOLVED:
        ticket.resolved_at = utc_now()
    db_session.commit()

    kwargs = (
        {"json": {"request_text": "Alter the request after execution"}} if method == "patch" else {}
    )
    response = getattr(client, method)(f"/api/tickets/{created['id']}", **kwargs)
    assert_error(response, 409)
    detail = client.get(f"/api/tickets/{created['id']}").json()
    assert detail["request_text"] == created["request_text"]
    assert detail["status"] == status.value


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_open_ticket_with_agent_history_is_immutable(
    client: TestClient, db_session: Session, method: str
) -> None:
    ticket = create_ticket(client)
    db_session.add(AgentRun(ticket_id=UUID(ticket["id"])))
    db_session.commit()

    kwargs = (
        {"json": {"request_text": "Alter a ticket with an existing run"}}
        if method == "patch"
        else {}
    )
    response = getattr(client, method)(f"/api/tickets/{ticket['id']}", **kwargs)
    assert_error(response, 409)
    assert (
        client.get(f"/api/tickets/{ticket['id']}").json()["request_text"] == ticket["request_text"]
    )


def test_deleting_draft_removes_initial_message(client: TestClient, db_session: Session) -> None:
    ticket = create_ticket(client)
    assert client.delete(f"/api/tickets/{ticket['id']}").status_code == 204
    remaining = db_session.scalar(
        select(func.count())
        .select_from(ConversationMessage)
        .where(ConversationMessage.ticket_id == UUID(ticket["id"]))
    )
    assert remaining == 0

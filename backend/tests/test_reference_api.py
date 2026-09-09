import pytest
from fastapi.testclient import TestClient


def test_health_is_available(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


@pytest.mark.parametrize("resource,minimum", [("employees", 10), ("repositories", 5)])
def test_seeded_reference_data_is_paginated(
    client: TestClient, resource: str, minimum: int
) -> None:
    response = client.get(f"/api/{resource}", params={"limit": 2, "offset": 0})
    assert response.status_code == 200
    first = response.json()
    assert first["total"] >= minimum
    assert first["limit"] == 2
    assert first["offset"] == 0
    assert len(first["items"]) == 2

    second = client.get(f"/api/{resource}", params={"limit": 2, "offset": 2}).json()
    assert second["total"] == first["total"]
    assert second["items"] != first["items"]


@pytest.mark.parametrize("resource", ["employees", "repositories"])
def test_reference_data_rejects_oversized_page(client: TestClient, resource: str) -> None:
    response = client.get(f"/api/{resource}", params={"limit": 101})
    assert response.status_code == 422
    assert response.json()["error"]["code"]

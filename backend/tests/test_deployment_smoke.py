import argparse
import importlib.util
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "smoke_deployment", Path(__file__).resolve().parents[1] / "scripts" / "smoke_deployment.py"
)
smoke_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke_module)
DeploymentSmoke, SmokeFailure = smoke_module.DeploymentSmoke, smoke_module.SmokeFailure


def public_ready():
    return {
        "status": "ready",
        "schema_revision": "test-revision",
        "llm_provider": "demo",
        "embedding_provider": "local_hash",
        "demo_auth_enabled": True,
        "agent_worker_enabled": True,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_provider", "openai"),
        ("embedding_provider", "openai"),
        ("agent_worker_enabled", False),
        ("demo_auth_enabled", False),
        ("llm_provider", None),
    ],
)
def test_smoke_rejects_paid_or_unavailable_runtime_before_writes(field, value):
    requests = []
    ready = public_ready() | {field: value}

    def respond(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=ready)

    with httpx.Client(
        base_url="http://demo.test", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(SmokeFailure, match="No write was submitted"):
            DeploymentSmoke(client, 10).run(None)
    assert requests == [("GET", "/api/ready")]


def test_smoke_refuses_existing_tickets_without_reset_or_mutation():
    requests = []

    def respond(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "/api/ready": public_ready(),
                "/api/health": {"status": "ok"},
                "/api/tickets": {"total": 1, "items": []},
            }[request.url.path],
        )

    with httpx.Client(
        base_url="http://demo.test", transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(SmokeFailure, match="fresh, disposable"):
            DeploymentSmoke(client, 10).run(None)
    assert all(method == "GET" for method, _ in requests)


def test_smoke_does_not_retry_uncertain_write_or_expose_transport_details():
    requests = []

    def fail(request):
        requests.append(request)
        raise httpx.ReadTimeout("private transport detail", request=request)

    with httpx.Client(base_url="http://demo.test", transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(SmokeFailure, match="write outcome is unknown") as error:
            DeploymentSmoke(client, 10).request("POST", "/api/tickets", json={})
    assert "private transport detail" not in str(error.value)
    assert len(requests) == 1


def test_smoke_poll_obeys_overall_deadline():
    clock = [0.0]

    def advance(seconds):
        clock[0] += seconds

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "RUNNING"}))
    with httpx.Client(base_url="http://demo.test", transport=transport) as client:
        smoke = DeploymentSmoke(client, 0.5, monotonic=lambda: clock[0], sleep=advance)
        with pytest.raises(SmokeFailure, match="overall timeout"):
            smoke.poll("/api/agent-runs/pending", lambda run: run["status"] == "COMPLETED")
    assert clock[0] == 0.5


@pytest.mark.parametrize(
    "url", ["https://user:password@demo.test", "https://demo.test/api", "file:///x"]
)
def test_smoke_requires_explicit_origin_without_embedded_credentials(url):
    with pytest.raises(argparse.ArgumentTypeError):
        smoke_module.deployment_url(url)

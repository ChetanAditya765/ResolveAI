"""Run real HTTP checks and a process restart against an isolated offline test database."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]


def serve(port: int, stop_file: Path) -> None:
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config("app.main:app", host="127.0.0.1", port=port, access_log=False)
    )

    def wait_for_stop() -> None:
        while not stop_file.exists():
            time.sleep(0.05)
        server.should_exit = True

    threading.Thread(target=wait_for_stop, daemon=True).start()
    server.run()


def run_command(environment: dict[str, str], *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", *arguments],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


@contextmanager
def running_server(environment: dict[str, str], port: int):
    with (
        tempfile.TemporaryDirectory(prefix="resolveai-server-") as directory,
        tempfile.TemporaryFile(mode="w+") as output,
    ):
        stop_file = Path(directory) / "stop"
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve",
                str(port),
                str(stop_file),
            ],
            cwd=BACKEND,
            env=environment,
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False
            ) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        output.seek(0)
                        raise RuntimeError(output.read())
                    try:
                        if client.get("/api/ready").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("API did not become ready within 15 seconds.")
                yield client
        finally:
            stop_file.touch()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    process.kill()
                process.wait(timeout=5)
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0, "Server did not stop."


def main(agent: bool = False, deployment: bool = False) -> None:
    agent = agent or deployment
    with tempfile.TemporaryDirectory(prefix="resolveai-http-") as directory:
        database = Path(directory) / "smoke.db"
        environment = {
            **os.environ,
            "APP_ENV": "test",
            "DEMO_AUTH_ENABLED": "true",
            "SESSION_SIGNING_KEY": "resolveai-isolated-smoke-session-signing-key",
            "AGENT_WORKER_ENABLED": "true" if agent else "false",
            "LLM_PROVIDER": "demo",
            "EMBEDDING_PROVIDER": "local_hash",
            "RAG_TOP_K": "8",
            "RAG_MIN_SCORE": "0.1",
            "CHECKPOINT_SQLITE_PATH": str(Path(directory) / "checkpoints.sqlite"),
            "DATABASE_URL": f"sqlite+pysqlite:///{database.as_posix()}",
        }
        run_command(environment, "alembic", "upgrade", "head")
        run_command(environment, "app.db.seed")
        if agent:
            run_command(environment, "app.rag.ingest")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        if deployment:
            from smoke_deployment import DeploymentSmoke

            with running_server(environment, port) as client:
                report = DeploymentSmoke(client, 180).run(None)
                ticket_path = f"/api/tickets/{report['ticket_id']}"
                ticket_snapshot = client.get(ticket_path).json()
                tools_path = f"/api/agent-runs/{report['run_id']}/tools"
                tool_snapshot = client.get(tools_path).json()
            with running_server(environment, port) as client:
                assert client.get(ticket_path).json() == ticket_snapshot
                assert client.get(tools_path).json() == tool_snapshot
                live = client.get("/api/evaluations/summary?source=live").json()
                assert live["total_results"] == live["passed_results"] == 1
                assert live["metrics"]["task_success"] == 1
                batch = client.get(f"/api/evaluations/batches/{report['batch_id']}").json()
                assert batch["status"] == "COMPLETED"
                assert batch["completed_count"] == report["scenario_count"]
                scenarios = client.get("/api/evaluations/summary?source=scenario").json()
                assert scenarios["passed_results"] == report["scenario_count"]
            report["persistence_after_process_restart"] = True
            report["database"] = "isolated SQLite (offline smoke only)"
            print(json.dumps(report, indent=2))
            return
        with running_server(environment, port) as client:
            assert client.get("/api/health").json()["status"] == "ok"
            assert client.get("/api/employees").json()["total"] == 10
            assert client.get("/api/repositories").json()["total"] == 5
            response = client.post(
                "/api/tickets",
                json={
                    "employee_id": "EMP001",
                    "request_text": "I need write access to the payments repository.",
                },
            )
            assert response.status_code == 201, response.text
            ticket = response.json()
            assert ticket["status"] == "OPEN"
            assert ticket["final_response"] is None
            assert response.headers["x-request-id"]
            assert (
                client.patch(
                    f"/api/tickets/{ticket['id']}", json={"status": "RESOLVED"}
                ).status_code
                == 422
            )
            if agent:
                accepted = client.post(f"/api/tickets/{ticket['id']}/run")
                assert accepted.status_code == 202, accepted.text
                run_id = accepted.json()["run_id"]
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    run = client.get(f"/api/agent-runs/{run_id}").json()
                    if run["status"] in {"WAITING_FOR_APPROVAL", "COMPLETED", "FAILED"}:
                        break
                    time.sleep(0.1)
                assert run["status"] == "WAITING_FOR_APPROVAL", run
                assert run["state"]["current_permission"] == "read"
                assert run["state"]["requested_permission"] == "write"
                assert run["state"]["outcome"] is None
                assert run["state"]["decision"]["disposition"] == "approval_required"
                assert "Repository Access Policy §3" in run["state"]["decision"]["summary"]
                assert run["state"]["retrieved_policies"]
                assert client.get(f"/api/agent-runs/{run_id}/steps").json()["total"] == 9
                assert client.get(f"/api/agent-runs/{run_id}/tools").json()["total"] == 6
                manager = next(
                    item
                    for item in client.get("/api/demo/users").json()
                    if item["employee_id"] == "EMP002"
                )
                token = client.post("/api/demo/session", json={"user_id": manager["id"]}).json()[
                    "access_token"
                ]
                headers = {"Authorization": "Bearer " + token}
                policies = client.get("/api/policies").json()
                assert policies["total"] == 8
                search = client.post(
                    "/api/policies/search",
                    json={"query": "write access manager approval", "top_k": 3},
                )
                assert search.status_code == 200
                assert search.json()["evidence"]
        with running_server(environment, port) as client:
            response = client.get(f"/api/tickets/{ticket['id']}")
            assert response.status_code == 200
            detail = response.json()
            assert detail["request_text"] == ticket["request_text"]
            assert detail["messages"][0]["content"] == ticket["request_text"]
            assert len(detail["messages"]) == 1
            if agent:
                assert detail["status"] == "WAITING_FOR_APPROVAL"
                assert (
                    client.get(f"/api/agent-runs/{run_id}").json()["status"]
                    == "WAITING_FOR_APPROVAL"
                )
                recovered = client.get(f"/api/agent-runs/{run_id}").json()
                assert (
                    recovered["state"]["retrieved_policies"] == run["state"]["retrieved_policies"]
                )
                approval_id = run["state"]["approval_id"]
                approval = client.get(f"/api/approvals/{approval_id}", headers=headers).json()
                assert approval["can_decide"]
                accepted = client.post(
                    f"/api/approvals/{approval_id}/approve",
                    headers=headers,
                    json={"comment": "Demo business need confirmed."},
                )
                assert accepted.status_code == 200, accepted.text
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    finished = client.get(f"/api/agent-runs/{run_id}").json()
                    if finished["status"] in {"COMPLETED", "FAILED"}:
                        break
                    time.sleep(0.1)
                assert finished["status"] == "COMPLETED", finished
                assert finished["state"]["outcome"] == "granted"
                assert finished["state"]["verification_result"]["observed_permission"] == "write"
                assert finished["state"]["verification_result"]["sufficient"]
                assert client.get(f"/api/agent-runs/{run_id}/steps").json()["total"] == 14
                assert client.get(f"/api/agent-runs/{run_id}/tools").json()["total"] == 10
                detail = client.get(f"/api/tickets/{ticket['id']}").json()
                assert detail["status"] == "RESOLVED"
                assert len(detail["messages"]) == 2
            assert "/api/tickets" in client.get("/openapi.json").json()["paths"]
        if agent:
            with running_server(environment, port) as client:
                durable = client.get(f"/api/tickets/{ticket['id']}").json()
                assert durable == detail
                assert (
                    client.get(f"/api/approvals/{approval_id}", headers=headers).json()["status"]
                    == "APPROVED"
                )
                assert client.get(f"/api/agent-runs/{run_id}/tools").json()["total"] == 10
        print(
            json.dumps(
                {
                    "status": "passed",
                    "database": "isolated SQLite (offline smoke only)",
                    "ticket_status": detail["status"],
                    "http_create": 201,
                    "persistence_after_process_restart": True,
                    "message_count": len(detail["messages"]),
                    "agent_run": "completed" if agent else "not requested",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), Path(sys.argv[3]))
    else:
        main(agent="--agent" in sys.argv, deployment="--deployment" in sys.argv)

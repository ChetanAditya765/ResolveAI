"""Exercise a disposable, freshly seeded demo deployment through its public HTTP API.

This creates one ticket, approves payments write access, and runs a scenario batch.
It never resets permissions or retries writes. Use a new deployment for each run.
No application imports, database credentials, or paid model calls are required.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

DEFAULT_SCENARIOS = (
    "read_engineering",
    "write_payments_approved",
    "write_payments_rejected",
    "admin_payments",
)
REQUEST_TEXT = "I need write access to the payments repository."


class SmokeFailure(RuntimeError):
    pass


def require(condition: object, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def deployment_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        valid_port = False
    if not (
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and valid_port
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    ):
        raise argparse.ArgumentTypeError("Use an HTTP(S) origin without credentials or a path.")
    return value.rstrip("/")


def identifier(record: dict, key: str = "id") -> str:
    try:
        return str(UUID(record[key]))
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise SmokeFailure(f"Response has no valid {key} UUID.") from error


def tool_output(tool: dict) -> dict:
    result = (tool.get("result") or {}).get("tool_result") or {}
    require(
        tool.get("status") == "SUCCEEDED"
        and result.get("succeeded") is True
        and result.get("execution_id") == tool.get("id")
        and result.get("tool_name") == tool.get("tool_name"),
        f"Tool {tool.get('tool_name')} has no matching successful execution record.",
    )
    return result.get("output") or {}


class DeploymentSmoke:
    def __init__(
        self,
        client: httpx.Client,
        timeout_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.monotonic, self.sleep = monotonic, sleep
        self.started = monotonic()
        self.deadline = self.started + timeout_seconds
        self.report: dict[str, Any] = {"status": "running"}

    def remaining(self) -> float:
        remaining = self.deadline - self.monotonic()
        require(remaining > 0, "Deployment smoke exceeded its overall timeout.")
        return remaining

    def request(self, method: str, path: str, *, expected: int = 200, **kwargs: Any) -> Any:
        try:
            response = self.client.request(
                method, path, timeout=min(10, self.remaining()), **kwargs
            )
        except httpx.RequestError as error:
            suffix = (
                " The write outcome is unknown; inspect the deployment before running again."
                if method != "GET"
                else ""
            )
            raise SmokeFailure(
                f"{method} {path} failed ({type(error).__name__}).{suffix}"
            ) from error
        require(
            response.status_code == expected,
            f"{method} {path} returned HTTP {response.status_code}; expected {expected}.",
        )
        try:
            return response.json()
        except ValueError as error:
            raise SmokeFailure(
                f"{method} {path} returned invalid JSON; no write is retried."
            ) from error

    def poll(self, path: str, accept: Callable[[Any], bool], **kwargs: Any) -> Any:
        while True:
            result = self.request("GET", path, **kwargs)
            if accept(result):
                return result
            self.sleep(min(0.25, self.remaining()))

    def ready(self) -> None:
        # Only readiness probes retry transport failures or an unavailable startup dependency.
        while True:
            try:
                response = self.client.get("/api/ready", timeout=min(5, self.remaining()))
            except httpx.RequestError:
                response = None
            if response is not None and response.status_code == 200:
                try:
                    ready = response.json()
                except ValueError as error:
                    raise SmokeFailure("Readiness returned invalid JSON.") from error
                break
            if response is not None:
                require(
                    response.status_code in {502, 503, 504},
                    f"Readiness returned HTTP {response.status_code}.",
                )
            self.sleep(min(0.25, self.remaining()))
        require(ready.get("status") == "ready", "The deployment is not ready.")
        require(
            ready.get("llm_provider") == "demo"
            and ready.get("embedding_provider") == "local_hash"
            and ready.get("demo_auth_enabled") is True
            and ready.get("agent_worker_enabled") is True,
            "Smoke requires demo/local_hash providers, demo sessions, and an enabled worker "
            "in the readiness response. No write was submitted.",
        )
        require(self.request("GET", "/api/health").get("status") == "ok", "Health check failed.")
        self.report["schema_revision"] = ready.get("schema_revision")

    def preflight(self, selected: list[str] | None) -> tuple[dict, dict, dict, list[str]]:
        self.ready()
        for path in ("/api/tickets", "/api/evaluations/batches"):
            require(
                self.request("GET", path, params={"limit": 1})["total"] == 0,
                "Smoke requires a fresh, disposable seeded deployment with no tickets or batches. "
                "It never resets existing permissions or removes records.",
            )
        employees = self.request("GET", "/api/employees", params={"limit": 100})
        repositories = self.request("GET", "/api/repositories", params={"limit": 100})
        require(
            employees["total"] >= 10 and repositories["total"] >= 5, "Demo catalog is incomplete."
        )
        employee = next((e for e in employees["items"] if e["id"] == "EMP001"), {})
        require(employee.get("manager_id") == "EMP002", "Chetan's seeded manager is missing.")
        repository = next((r for r in repositories["items"] if r["name"] == "payments"), {})
        require(repository.get("owning_department") == "Engineering", "Payments seed is missing.")
        identifier(repository)
        users = self.request("GET", "/api/demo/users")
        employee_user = next((u for u in users if u["employee_id"] == "EMP001"), {})
        manager = next((u for u in users if u["employee_id"] == "EMP002"), {})
        require(
            employee_user.get("role") == "employee" and manager.get("role") == "manager",
            "Seeded employee and manager demo identities are required.",
        )
        catalog = self.request("GET", "/api/evaluations/scenarios")
        available = {item["id"] for item in catalog}
        require(len(available) >= 25, "Evaluation catalog has fewer than 25 scenarios.")
        scenario_ids = selected if selected is not None else [item["id"] for item in catalog]
        require(
            scenario_ids
            and len(set(scenario_ids)) == len(scenario_ids)
            and set(scenario_ids) <= available,
            "Requested scenario IDs are missing from the catalog or duplicated.",
        )
        return employee_user, manager, repository, scenario_ids

    def login(self, user: dict) -> dict[str, str]:
        session = self.request("POST", "/api/demo/session", json={"user_id": identifier(user)})
        require(session.get("user", {}).get("id") == user["id"], "Demo session identity mismatch.")
        token = session.get("access_token")
        require(isinstance(token, str) and token, "Demo session has no access token.")
        return {"Authorization": f"Bearer {token}"}

    def wait_run(self, run_id: str, expected: str) -> dict:
        def accept(run: dict) -> bool:
            require(run.get("provider_name") == "demo", "Agent run is not using the demo provider.")
            require(
                run["state"]["retrieval_config"]["provider"] == "local_hash",
                "Agent run is not using offline embeddings.",
            )
            status = run.get("status")
            require(
                status == expected or status in {"PENDING", "RUNNING"},
                f"Agent reached {status}; expected {expected}.",
            )
            return status == expected

        return self.poll(f"/api/agent-runs/{run_id}", accept)

    def payments(self, employee: dict, manager: dict, repository: dict) -> dict[str, str]:
        employee_headers, manager_headers = self.login(employee), self.login(manager)
        ticket = self.request(
            "POST",
            "/api/tickets",
            expected=201,
            headers=employee_headers,
            json={"employee_id": "EMP001", "request_text": REQUEST_TEXT},
        )
        ticket_id = self.report["ticket_id"] = identifier(ticket)
        accepted = self.request("POST", f"/api/tickets/{ticket_id}/run", expected=202)
        run_id = self.report["run_id"] = identifier(accepted, "run_id")
        paused = self.wait_run(run_id, "WAITING_FOR_APPROVAL")
        state = paused["state"]
        require(
            state.get("current_permission") == "read"
            and state.get("requested_permission") == "write"
            and state.get("employee", {}).get("id") == "EMP001"
            and state.get("repository", {}).get("id") == repository["id"],
            "Paused request does not match the fresh seeded payments write demo.",
        )
        require(
            state["decision"]["disposition"] == "approval_required"
            and "Repository Access Policy §3" in state["decision"]["summary"]
            and state.get("retrieved_policies"),
            "Write approval is not grounded in the retrieved Repository Access Policy.",
        )
        before = self.request("GET", f"/api/agent-runs/{run_id}/tools", params={"limit": 100})
        require(
            not any(t["tool_name"] == "grant_repository_permission" for t in before["items"]),
            "A permission mutation was attempted before manager approval.",
        )
        approval_id = self.report["approval_id"] = identifier(state, "approval_id")
        approval_path = f"/api/approvals/{approval_id}"
        approval = self.request("GET", approval_path, headers=manager_headers)
        require(
            approval.get("can_decide") is True
            and approval.get("status") == "PENDING"
            and approval.get("approver_id") == manager["id"]
            and approval.get("ticket_id") == ticket_id
            and approval.get("run_id") == run_id,
            "Manager cannot decide the matching pending approval.",
        )
        approved = self.request(
            "POST",
            approval_path + "/approve",
            headers=manager_headers,
            json={"comment": "Disposable deployment smoke: payments business need confirmed."},
        )
        require(
            approved.get("status") == "APPROVED" and approved.get("decided_by_id") == manager["id"],
            "Manager approval was not recorded.",
        )
        finished = self.wait_run(run_id, "COMPLETED")
        detail = self.request("GET", f"/api/tickets/{ticket_id}")
        require(
            finished["state"].get("outcome") == "granted"
            and detail.get("status") == "RESOLVED"
            and detail.get("final_response")
            and detail.get("resolved_at"),
            "The payments ticket did not resolve with a final response.",
        )
        self.verify_records(finished, repository["id"])
        results = self.poll(
            "/api/evaluations/results",
            lambda page: page["total"] > 0,
            params={"source": "live", "run_id": run_id},
        )
        require(results["total"] == 1, "Run has duplicate live evaluations.")
        result = results["items"][0]
        assertions = {a["name"]: a["passed"] for a in result["assertions"]}
        require(
            result["passed"] is True
            and result["metrics"].get("task_success") == 1
            and all(
                assertions.get(name) is True
                for name in (
                    "tool_scope",
                    "resource_grounding",
                    "policy_evidence",
                    "approval_before_grant",
                    "verification_before_close",
                )
            ),
            "Payments evaluation failed policy, approval, tool, or verification assertions.",
        )
        self.report["evaluation_id"] = identifier(result)
        return manager_headers

    def verify_records(self, run: dict, repository_id: str) -> None:
        steps = self.request("GET", f"/api/agent-runs/{run['id']}/steps", params={"limit": 100})
        tools = self.request("GET", f"/api/agent-runs/{run['id']}/tools", params={"limit": 100})
        nodes = {s["node"]: s for s in steps["items"]}
        required_nodes = (
            "RECEIVE_REQUEST",
            "IDENTIFY_EMPLOYEE",
            "IDENTIFY_RESOURCE",
            "RETRIEVE_POLICY",
            "CHECK_CURRENT_ACCESS",
            "REQUEST_APPROVAL",
            "AWAIT_APPROVAL",
            "CHECK_APPROVAL",
            "EXECUTE_ACTION",
            "VERIFY_ACTION",
            "RESOLVE",
        )
        require(
            all(nodes.get(n, {}).get("status") == "COMPLETED" for n in required_nodes),
            "The persisted timeline omits a completed required workflow step.",
        )
        by_id = {t["id"]: t for t in tools["items"]}
        state = run["state"]
        grant = by_id.get(state["execution_result"]["tool_execution_id"], {})
        verify = by_id.get(state["verification_result"]["tool_execution_id"], {})
        output = tool_output(grant)
        require(
            grant.get("tool_name") == "grant_repository_permission"
            and grant.get("arguments")
            == {
                "employee_id": "EMP001",
                "repository_id": repository_id,
                "permission": "write",
                "approval_id": self.report["approval_id"],
            }
            and output.get("changed") is True
            and output.get("previous_permission") == "read"
            and output.get("observed_permission") == "write",
            "The audited grant does not show the approved read-to-write change.",
        )
        require(
            verify.get("tool_name") == "get_repository_permission"
            and verify.get("step_id") == nodes["VERIFY_ACTION"]["id"]
            and tool_output(verify)
            == {
                "employee_id": "EMP001",
                "repository_id": repository_id,
                "permission": "write",
            }
            and state["verification_result"].get("sufficient") is True,
            "The recorded verification does not independently confirm write permission.",
        )
        self.report.update(
            step_count=steps["total"], tool_count=tools["total"], ticket_status="RESOLVED"
        )

    def scenarios(self, scenario_ids: list[str], headers: dict[str, str]) -> None:
        submission_key = self.report["submission_key"] = str(uuid4())
        batch = self.request(
            "POST",
            "/api/evaluations/run",
            expected=202,
            headers=headers,
            json={"scenario_ids": scenario_ids, "submission_key": submission_key},
        )
        batch_id = self.report["batch_id"] = identifier(batch)

        def completed(value: dict) -> bool:
            require(value["status"] != "FAILED", "Scenario batch failed; inspect its saved error.")
            return value["status"] == "COMPLETED"

        batch = self.poll(f"/api/evaluations/batches/{batch_id}", completed)
        require(
            batch["completed_count"] == batch["total_count"] == len(scenario_ids),
            "Batch completed without every selected scenario.",
        )
        results = self.request(
            "GET",
            "/api/evaluations/results",
            params={"source": "scenario", "batch_id": batch_id, "limit": 100},
        )
        require(
            results["total"] == len(scenario_ids)
            and {r["scenario_id"] for r in results["items"]} == set(scenario_ids)
            and all(r["passed"] is True for r in results["items"]),
            "One or more scenario results are missing or failed assertions.",
        )
        summary = self.request(
            "GET",
            "/api/evaluations/summary",
            params={"source": "scenario", "batch_id": batch_id},
        )
        require(
            summary["total_results"] == summary["passed_results"] == len(scenario_ids)
            and summary["metrics"].get("task_success") == 1,
            "Scenario aggregate metrics do not match the saved results.",
        )
        for result in results["items"]:
            detail = self.request("GET", f"/api/evaluations/results/{identifier(result)}")
            require(
                detail.get("expected")
                and detail["trace"]["steps"]
                and detail["trace"]["run"].get("provider_name") == "demo"
                and detail["trace"]["ticket"].get("id") != self.report["ticket_id"],
                "A scenario lacks its isolated expected outcome and persisted execution trace.",
            )
        require(
            self.request("GET", "/api/tickets", params={"limit": 1})["total"] == 1,
            "Scenario execution leaked tickets into the demo application database.",
        )
        self.report.update(scenario_count=len(scenario_ids), scenario_metrics=summary["metrics"])

    def run(self, selected: list[str] | None) -> dict:
        employee, manager, repository, scenario_ids = self.preflight(selected)
        manager_headers = self.payments(employee, manager, repository)
        self.scenarios(scenario_ids, manager_headers)
        self.report.update(
            status="passed", elapsed_seconds=round(self.monotonic() - self.started, 2)
        )
        return self.report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, type=deployment_url)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--scenario-id", action="append", help="Run a selected case; repeat for a subset."
    )
    group.add_argument(
        "--all-scenarios", action="store_true", help="Run the complete scenario catalog."
    )
    args = parser.parse_args()
    if not 10 <= args.timeout_seconds <= 1800:
        parser.error("--timeout-seconds must be between 10 and 1800")
    selected = None if args.all_scenarios else args.scenario_id or list(DEFAULT_SCENARIOS)
    with httpx.Client(base_url=args.base_url, follow_redirects=False, trust_env=False) as client:
        smoke = DeploymentSmoke(client, args.timeout_seconds)
        try:
            print(json.dumps(smoke.run(selected), indent=2))
        except (SmokeFailure, KeyError, TypeError, ValueError) as error:
            smoke.report.update(status="failed", error=str(error))
            print(json.dumps(smoke.report, indent=2), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

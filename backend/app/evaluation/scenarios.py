"""Versioned, reviewed access cases; fixture controls are never accepted by the API."""

from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field

from app.evaluation.contracts import ExpectedOutcome


class Fault(StrEnum):
    NONE = "none"
    GRANT_FAILURE = "grant_failure"
    VERIFICATION_FAILURE = "verification_failure"
    POLICY_FAILURE = "policy_failure"
    EMPTY_POLICY = "empty_policy"
    LLM_TIMEOUT = "llm_timeout"
    MALFORMED_OUTPUT = "malformed_output"
    EMPLOYEE_LOOKUP = "employee_lookup"
    INACTIVE_EMPLOYEE = "inactive_employee"
    INACTIVE_MANAGER = "inactive_manager"
    INACTIVE_REVIEWER = "inactive_reviewer"
    MISSING_APPROVAL = "missing_approval"
    CHANGED_POLICY = "changed_policy"
    TOOL_SCOPE = "tool_scope"


class Scenario(ExpectedOutcome):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    description: str
    employee_id: str
    request_text: str
    repository_name: str | None = Field(default=None, exclude=True)
    initial_permission: Literal["none", "read", "write", "admin"] | None = Field(
        default=None, exclude=True
    )
    approval_decision: Literal["APPROVED", "REJECTED"] | None = Field(default=None, exclude=True)
    fault: Fault = Field(default=Fault.NONE, exclude=True)


LOOKUP = ["get_employee", "find_repository", "get_repository_permission", "search_policies"]
GRANT = [*LOOKUP, "grant_repository_permission", "close_ticket"]
APPROVE = [*GRANT, "create_approval_request", "get_approval_status"]
REJECT = [*LOOKUP, "create_approval_request", "get_approval_status", "escalate_ticket"]
NO_CHANGE = [*LOOKUP, "close_ticket"]
NO_MUTATION = ["grant_repository_permission", "close_ticket"]
READ = "§2. Read access within a department"
WRITE = "§3. Write access"
CROSS = "§4. Cross-department access"
ADMIN = "§5. Admin access"
EXISTING = "§2. Existing permissions"


def case(
    key: str,
    name: str,
    description: str,
    *,
    employee: str = "EMP001",
    repository: str | None = "payments",
    permission: str = "write",
    text: str | None = None,
    outcome: str = "granted",
    final_permission: str | None = "write",
    tools: list[str] | None = None,
    forbidden: list[str] | None = None,
    sections: list[str] | None = None,
    approval: Literal["APPROVED", "REJECTED"] | None = None,
    initial: Literal["none", "read", "write", "admin"] | None = None,
    fault: Fault = Fault.NONE,
) -> Scenario:
    return Scenario(
        id=key,
        name=name,
        description=description,
        employee_id=employee,
        request_text=text or f"I need {permission} access to the {repository} repository.",
        repository_name=repository,
        expected_outcome=outcome,
        expected_final_status="RESOLVED"
        if outcome in {"granted", "already_sufficient"}
        else "ESCALATED",
        expected_tools=tools if tools is not None else APPROVE if approval else GRANT,
        forbidden_tools=forbidden or [],
        requires_approval=approval is not None,
        expected_permission=final_permission,
        expected_policy_sections=sections or [],
        initial_permission=initial,
        approval_decision=approval,
        fault=fault,
    )


SCENARIOS: tuple[Scenario, ...] = (
    case(
        "read_engineering",
        "Engineering read access",
        "A platform engineer receives same-department read access without a reviewer.",
        employee="EMP007",
        permission="read",
        final_permission="read",
        sections=[READ],
        forbidden=["create_approval_request", "escalate_ticket"],
    ),
    case(
        "read_finance",
        "Finance read access",
        "A finance analyst with no existing grant receives read access to finance-reporting.",
        employee="EMP004",
        repository="finance-reporting",
        permission="read",
        initial="none",
        final_permission="read",
        sections=[READ],
        forbidden=["create_approval_request", "escalate_ticket"],
    ),
    case(
        "read_people_operations",
        "People operations read access",
        "A people operations employee receives departmental read access after the fixture "
        "removes an existing grant.",
        employee="EMP006",
        repository="people-ops",
        permission="read",
        initial="none",
        final_permission="read",
        sections=[READ],
        forbidden=["create_approval_request", "escalate_ticket"],
    ),
    case(
        "read_security",
        "Security read access",
        "The Security department receives read only on its restricted repository, consistent "
        "with the v0.1 policy.",
        employee="EMP003",
        repository="security-audit",
        permission="read",
        initial="none",
        final_permission="read",
        sections=[READ],
        forbidden=["create_approval_request", "escalate_ticket"],
    ),
    case(
        "write_payments_approved",
        "Payments write approved",
        "The primary demo upgrades Chetan from read to write only after Maya records approval.",
        approval="APPROVED",
        sections=[WRITE],
        forbidden=["escalate_ticket"],
    ),
    case(
        "write_platform_approved",
        "Platform write approved",
        "An existing platform reader receives a manager-approved write upgrade.",
        repository="platform-api",
        approval="APPROVED",
        sections=[WRITE],
        forbidden=["escalate_ticket"],
    ),
    case(
        "write_finance_approved",
        "Finance write approved",
        "A Finance manager reviews a finance-reporting write upgrade for their direct report.",
        employee="EMP004",
        repository="finance-reporting",
        approval="APPROVED",
        sections=[WRITE],
        forbidden=["escalate_ticket"],
    ),
    case(
        "write_payments_rejected",
        "Payments write rejected",
        "A rejected write request preserves Chetan's read permission and records escalation.",
        approval="REJECTED",
        outcome="rejected",
        final_permission="read",
        tools=REJECT,
        forbidden=NO_MUTATION,
        sections=[WRITE],
    ),
    case(
        "write_platform_rejected",
        "Platform write rejected",
        "Rejection on the internal platform repository must not execute the grant tool.",
        repository="platform-api",
        approval="REJECTED",
        outcome="rejected",
        final_permission="read",
        tools=REJECT,
        forbidden=NO_MUTATION,
        sections=[WRITE],
    ),
    case(
        "cross_department_read_approved",
        "Cross-department read approved",
        "An Engineering employee needs a recorded manager decision before reading Finance data.",
        repository="finance-reporting",
        permission="read",
        approval="APPROVED",
        final_permission="read",
        sections=[CROSS],
    ),
    case(
        "cross_department_read_rejected",
        "Cross-department read rejected",
        "A Finance employee's rejected payments request preserves the absence of access.",
        employee="EMP004",
        permission="read",
        approval="REJECTED",
        outcome="rejected",
        final_permission="none",
        tools=REJECT,
        forbidden=NO_MUTATION,
        sections=[CROSS],
    ),
    case(
        "cross_department_write_approved",
        "Cross-department write approved",
        "Engineering write access to security-audit must cite both write and cross-department "
        "rules.",
        repository="security-audit",
        approval="APPROVED",
        sections=[WRITE, CROSS],
    ),
    case(
        "admin_payments",
        "Payments admin prohibited",
        "Admin access is escalated to Security without creating an automated grant path.",
        permission="admin",
        outcome="escalated",
        final_permission="read",
        tools=[*LOOKUP, "escalate_ticket"],
        forbidden=[*NO_MUTATION, "create_approval_request"],
        sections=[ADMIN],
    ),
    case(
        "admin_existing",
        "Existing admin still escalates",
        "An existing Security administrator's admin request remains outside automatic closure.",
        employee="EMP003",
        repository="security-audit",
        permission="admin",
        outcome="escalated",
        final_permission="admin",
        tools=[*LOOKUP, "escalate_ticket"],
        forbidden=[*NO_MUTATION, "create_approval_request"],
        sections=[ADMIN],
    ),
    case(
        "admin_cross_department",
        "Cross-department admin prohibited",
        "A Finance repository admin request cannot use ordinary manager approval to bypass the "
        "admin prohibition.",
        repository="finance-reporting",
        permission="admin",
        outcome="escalated",
        final_permission="none",
        tools=[*LOOKUP, "escalate_ticket"],
        forbidden=[*NO_MUTATION, "create_approval_request"],
        sections=[ADMIN],
    ),
    case(
        "unknown_repository",
        "Unknown repository",
        "An absent repository is reported for clarification without inventing a catalog resource.",
        repository="phantom-ledger",
        outcome="escalated",
        final_permission=None,
        tools=["get_employee", "find_repository", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "search_policies", "create_approval_request"],
    ),
    case(
        "ambiguous_permission",
        "Permission needs clarification",
        "A request naming a repository without an access level cannot be executed.",
        text="I need access to the payments repository.",
        outcome="escalated",
        final_permission="read",
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
    case(
        "ambiguous_multiple_repositories",
        "Multiple resources need clarification",
        "A multi-repository request is outside the single-resource contract and must not be "
        "partially executed.",
        text="I need write access to payments and platform-api.",
        outcome="escalated",
        final_permission="read",
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
    case(
        "unsupported_request",
        "Unsupported IT request",
        "A printer repair request escalates instead of invoking repository permission tools.",
        text="Please repair my office printer.",
        repository=None,
        outcome="escalated",
        final_permission=None,
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
    case(
        "existing_read",
        "Existing read needs no change",
        "Chetan already has payments read access; independently verify it without requesting "
        "approval or writing permissions.",
        permission="read",
        outcome="already_sufficient",
        final_permission="read",
        tools=NO_CHANGE,
        forbidden=["grant_repository_permission", "create_approval_request", "escalate_ticket"],
        sections=[EXISTING],
    ),
    case(
        "existing_write",
        "Existing write needs no approval",
        "A platform engineer already has write access, so no extra review or grant is necessary.",
        employee="EMP007",
        repository="platform-api",
        outcome="already_sufficient",
        tools=NO_CHANGE,
        forbidden=["grant_repository_permission", "create_approval_request", "escalate_ticket"],
        sections=[EXISTING],
    ),
    case(
        "no_write_downgrade",
        "Read request preserves write",
        "A lower access request must not downgrade an existing write permission.",
        employee="EMP007",
        repository="platform-api",
        permission="read",
        outcome="already_sufficient",
        tools=NO_CHANGE,
        forbidden=["grant_repository_permission", "create_approval_request", "escalate_ticket"],
        sections=[EXISTING],
    ),
    case(
        "no_admin_downgrade",
        "Read request preserves existing admin",
        "Read verification may close with an existing admin grant but must never issue or "
        "alter admin permission.",
        employee="EMP003",
        repository="security-audit",
        permission="read",
        outcome="already_sufficient",
        final_permission="admin",
        tools=NO_CHANGE,
        forbidden=["grant_repository_permission", "create_approval_request", "escalate_ticket"],
        sections=[EXISTING],
    ),
    case(
        "grant_failure",
        "Grant service failure",
        "A failed mock permission service after approval must leave the original read grant "
        "and escalate.",
        approval="APPROVED",
        outcome="failed",
        final_permission="read",
        fault=Fault.GRANT_FAILURE,
        tools=[*REJECT, "grant_repository_permission"],
        forbidden=["close_ticket"],
        sections=[WRITE],
    ),
    case(
        "verification_failure",
        "Readback failure after grant",
        "The grant succeeds but the independent readback fails; retain the actual write effect "
        "without falsely resolving the ticket.",
        approval="APPROVED",
        outcome="failed",
        fault=Fault.VERIFICATION_FAILURE,
        tools=[*REJECT, "grant_repository_permission"],
        forbidden=["close_ticket"],
        sections=[WRITE],
    ),
    case(
        "policy_retrieval_failure",
        "Policy retrieval unavailable",
        "Even a departmental read request must stop when policy retrieval raises an error.",
        employee="EMP007",
        permission="read",
        outcome="failed",
        final_permission="none",
        fault=Fault.POLICY_FAILURE,
        tools=[*LOOKUP, "escalate_ticket"],
        forbidden=[*NO_MUTATION, "create_approval_request"],
    ),
    case(
        "policy_evidence_empty",
        "No applicable policy evidence",
        "A successful search with no evidence cannot authorize a permission change.",
        employee="EMP007",
        permission="read",
        outcome="escalated",
        final_permission="none",
        fault=Fault.EMPTY_POLICY,
        tools=[*LOOKUP, "escalate_ticket"],
        forbidden=[*NO_MUTATION, "create_approval_request"],
    ),
    case(
        "llm_timeout",
        "Provider timeout",
        "A deterministic provider timeout stops classification before resource selection or "
        "access changes.",
        outcome="failed",
        final_permission="read",
        fault=Fault.LLM_TIMEOUT,
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
    case(
        "malformed_structured_output",
        "Invalid structured output",
        "A malformed classification is rejected by the real Pydantic boundary and safely "
        "escalated.",
        outcome="failed",
        final_permission="read",
        fault=Fault.MALFORMED_OUTPUT,
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
    case(
        "employee_lookup_failure",
        "Directory identity missing",
        "The directory tool reports a missing identity for a valid submitted ticket; no "
        "replacement employee may be invented.",
        outcome="failed",
        final_permission="read",
        fault=Fault.EMPLOYEE_LOOKUP,
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
    case(
        "employee_inactive",
        "Employee deactivated before run",
        "An employee becomes inactive after submitting the ticket; the workflow rechecks "
        "current identity eligibility.",
        outcome="escalated",
        final_permission="read",
        fault=Fault.INACTIVE_EMPLOYEE,
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
    case(
        "manager_missing",
        "No recorded manager",
        "A VP with no recorded manager cannot receive new write permission automatically.",
        employee="EMP009",
        outcome="escalated",
        final_permission="none",
        tools=[*LOOKUP, "escalate_ticket"],
        forbidden=[*NO_MUTATION, "create_approval_request"],
        sections=[WRITE],
    ),
    case(
        "manager_inactive",
        "Recorded manager unavailable",
        "Approval creation rejects an inactive recorded manager before a pending request can "
        "be published.",
        outcome="escalated",
        final_permission="read",
        fault=Fault.INACTIVE_MANAGER,
        tools=[*LOOKUP, "create_approval_request", "escalate_ticket"],
        forbidden=NO_MUTATION,
        sections=[WRITE],
    ),
    case(
        "reviewer_deactivated",
        "Reviewer changed after approval",
        "A recorded approval is revalidated at execution when its reviewer has become inactive.",
        approval="APPROVED",
        outcome="failed",
        final_permission="read",
        fault=Fault.INACTIVE_REVIEWER,
        tools=[*REJECT, "grant_repository_permission"],
        forbidden=["close_ticket"],
        sections=[WRITE],
    ),
    case(
        "approval_missing_on_resume",
        "Approval record unavailable on resume",
        "The fixture removes the approval after a real decision; the resumed agent must not "
        "trust its notification or cached state.",
        approval="APPROVED",
        outcome="failed",
        final_permission="read",
        fault=Fault.MISSING_APPROVAL,
        tools=REJECT,
        forbidden=NO_MUTATION,
        sections=[WRITE],
    ),
    case(
        "policy_changed_after_approval",
        "Policy changed during review",
        "A policy source changes after approval, invalidating the evidence at the grant boundary.",
        approval="APPROVED",
        outcome="failed",
        final_permission="read",
        fault=Fault.CHANGED_POLICY,
        tools=[*REJECT, "grant_repository_permission"],
        forbidden=["close_ticket"],
        sections=[WRITE],
    ),
    case(
        "repository_tool_scope_mismatch",
        "Structured tool scope mismatch",
        "The provider proposes a different repository from its classification; the scope guard "
        "stops execution before lookup.",
        outcome="failed",
        final_permission="read",
        fault=Fault.TOOL_SCOPE,
        tools=["get_employee", "escalate_ticket"],
        forbidden=[*NO_MUTATION, "find_repository", "create_approval_request"],
    ),
)

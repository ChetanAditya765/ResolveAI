# Repository Access Policy

Version: 1.0
Owner: Engineering Operations
Classification: Internal
Effective date: 2026-01-01

## §1. Scope and identity

This policy applies to all simulated source-code repository access requests at Northstar Technologies. An access request must identify an active employee in the employee directory, one existing repository in the repository catalog, and a requested permission: read, write, or admin. A name in a request is not sufficient evidence that an employee or repository exists.

## §2. Read access within a department

An active employee may receive read access automatically when the employee's department exactly matches the repository's owning department. ResolveAI must retrieve this policy and check current access before granting access. Existing write or admin access already satisfies a read request and must not be downgraded. Repository sensitivity must be recorded in the decision summary; classification by itself does not override the explicit approval rules in this policy.

## §3. Write access

Every request for new write access requires approval from the requesting employee's recorded manager. This requirement applies even when the employee and repository are in the same department. Approval must cover the employee, repository, and exact requested permission. The agent must create a persistent approval request and pause before invoking the permission-grant tool. For example, Chetan's request for write access to the payments repository requires Engineering Manager Maya Rao's approval.

## §4. Cross-department access

Read or write access to a repository owned by another department requires the requesting employee's manager to approve. Cross-department read access is never automatic. A valid approval for the exact action satisfies both the write and cross-department requirements; duplicate approval requests are unnecessary. If the recorded manager is missing or inactive, escalate the request instead of inventing an approver.

## §5. Admin access

The agent must never grant admin access. Route every request for admin access to the Security Administration team, including requests from managers and security employees. Manager approval does not authorize an automated admin grant. See Privileged Access Policy.

## §6. Completion

After an authorized grant, read the employee's permission back from the access-management tool. Resolve the ticket only if the observed permission satisfies the request. A failed or inconclusive verification must produce an escalation or failure outcome, never a success message. Cite this policy and the applicable section in the stored decision summary.

# Least Privilege Policy

Version: 1.0
Owner: Information Security
Classification: Internal
Effective date: 2026-01-01

## §1. Permission ordering

Repository permissions are ordered none, read, write, admin. Grant only the permission requested and permitted by the applicable policy. A request for read access is not a request for write access. Never infer that a more privileged action is acceptable because it is convenient for the employee or the agent.

## §2. Existing permissions

Inspect current permission before planning a change. If an active employee already has the requested read or write permission, or a higher permission, do not call the grant tool. Report the verified existing access and close the ticket without a permission mutation. Do not downgrade access as a side effect of handling a less privileged request. Requests for admin permission must still be escalated under Privileged Access Policy.

## §3. Bound changes and idempotency

A permission record is unique for one employee and one repository. A retry must not create a second record or expand scope. A tool invocation must identify the employee ID, repository ID, and permission explicitly. Do not modify a different repository or employee in order to satisfy a request. Removing access is outside the ResolveAI v0.1 request workflow and must be escalated.

## §4. Decision evidence

Use current directory data, repository catalog data, retrieved policies, and tool results as evidence. Store a short decision summary and policy references. Do not store hidden model reasoning or infer approval from an employee's title or seniority.

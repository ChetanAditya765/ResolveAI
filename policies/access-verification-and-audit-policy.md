# Access Verification and Audit Policy

Version: 1.0
Owner: IT Service Management
Classification: Internal
Effective date: 2026-01-01

## §1. Verify the actual permission

After invoking grant_repository_permission, call get_repository_permission for the same employee and repository. A successful grant response alone is not proof that the permission changed. A read request is satisfied by read, write, or admin; a write request is satisfied by write or admin. Do not invoke the admin grant path. When existing permission is already sufficient, a successful permission read is the evidence for closure and no grant is needed.

## §2. Errors and uncertainty

If a lookup, approval check, permission grant, or verification tool fails, record the error and do not invent a successful result. If verification shows insufficient permission, times out, or returns an invalid result, leave the ticket failed or escalate it with a concise explanation. A model timeout or malformed model output is also a safe failure condition and must not authorize execution.

## §3. Closing a ticket

Use close_ticket only when the requested supported action has been verified or the employee's existing permission has been verified as sufficient. The final response must say what permission was observed, name the repository, and reference the policy used. If the request was rejected, ambiguous, unsupported, or blocked, explain that outcome without saying access was granted.

## §4. Audit and evaluation

Persist the original request, run status, timeline steps, policy references, tool executions, approval decisions, verification outcome, and final response. Store concise decision summaries rather than private chain-of-thought. Evaluate completed runs using observable facts: valid identity and repository, correct policy, authorized tool selection, approval compliance, verification, and the final ticket status. A safe escalation is a successful result when the request cannot be safely fulfilled.

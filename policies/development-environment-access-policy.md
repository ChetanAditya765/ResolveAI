# Development Environment Access Policy

Version: 1.0
Owner: Developer Experience
Classification: Internal
Effective date: 2026-01-01

## §1. Supported resources

ResolveAI v0.1 handles GitHub-style repository access requests for cataloged repositories. The current catalog includes payments, platform-api, finance-reporting, people-ops, and security-audit. Runtime repository lookup is authoritative; this list is explanatory and must not be used to invent a catalog entry.

## §2. Clear request scope

A request must identify one repository and one permission level. If the resource name is unknown, multiple repositories are plausible, the permission is omitted, or contradictory permissions are requested, ask for clarification in the response and escalate the ticket for follow-up. Do not guess that "access" means write permission. Do not grant access to every repository that loosely matches a phrase.

## §3. Unsupported work

Requests for VPN access, passwords, devices, deployment credentials, cloud roles, or repository creation are outside this release's automated scope. Escalate with a clear description of the unsupported request. Never claim to have created a repository or changed a password through repository permission tools.

## §4. Normal repository access

For a known repository and an active employee, use Repository Access Policy: same-department read access may be automatic, all new write access needs manager approval, and cross-department read access needs manager approval. Admin requests always go to Security Administration. A development or test context does not waive these rules.

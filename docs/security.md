# Security boundaries

ResolveAI v0.1 is a localhost demonstration of a bounded repository-access workflow.
Do not deploy its writable API as a public service without additional security work.

## Enforced workflow controls

The provider interprets a request and proposes a typed tool selection. The service binds identity
to the stored requester, grounds repositories in the catalog, and independently computes policy.
Retrieved chunks must match reviewed policy fingerprints; arbitrary retrieved prose cannot
authorize a permission change. Tools enforce run scope, active lease and designated graph node.

Write and cross-department access require an eligible recorded approval. Self-approval is blocked.
Admin requests escalate. The mutation tool rechecks identity, policy and approval scope. A separate
permission read and matching closure evidence are required to resolve the ticket.

## What these controls do not provide

Demo bearer sessions and the identity selector are not enterprise authentication. Some public
routes lack production authorization. There is no tenant isolation, production GitHub/Jira adapter,
enterprise SSO, or general autonomous tool discovery. Database administrators can modify stored
evidence; it is not a tamper-proof ledger. Local defaults are intentionally demo credentials.

Before public application hosting, implement real authentication and comprehensive route/resource
authorization, secret management, abuse controls, operational monitoring, and external-effect
reconciliation. Validate these independently. In the meantime share the static case study and video.

## Local configuration

Use `.env.example` as a safe template. Keep `.env`, credentials, databases, logs and private
development files excluded from Git and Docker build contexts. Use dedicated disposable databases
for acceptance testing. No live provider key is required for the default demonstration.

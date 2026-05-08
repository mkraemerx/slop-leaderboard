---
ID: ASSUMPTION-008
Category: Architectural
Status: open
Created: 2026-05-08
Context: Requirements rework — root repo + fork tracking model
Level: 1
---

# Fork discovery uses the platform REST API; a token is required

## Assumption

To list all forks of the root repository, the system calls the GitHub or GitLab REST API. This requires a personal access token (read-only, `repo` scope on GitHub) provided at configuration time.

## Rationale

There is no way to enumerate forks from a local Git clone — fork relationships are metadata held by the hosting platform. The requirement says "discovers all forks automatically", which implies API access.

## Alternatives Considered

- **Manual-only fork registration**: Simpler, no token needed, but requires the instructor to add each participant's fork manually — poor UX for a class of 20+.
- **Webhook-based discovery**: Forks register themselves by sending a first webhook — requires participants to configure their fork, adding setup friction.

## Notes

- The token is stored in application config (environment variable `PLATFORM_TOKEN`), never in the database or UI.
- If the API is unavailable or the token is missing, manual fork addition must still work as a fallback (FR-01 acceptance criterion).
- GitLab fork listing API differs from GitHub's; the platform type (`github` / `gitlab`) must also be configurable.

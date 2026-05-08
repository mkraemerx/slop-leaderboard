---
ID: ASSUMPTION-004
Category: Architectural
Status: open
Created: 2026-05-07
Context: Initial requirements definition — sync strategy (updated: fork model, 2026-05-08)
Level: 1
---

# Polling (60-minute interval) is the primary sync mechanism; webhooks are optional enhancement

## Assumption

The default data-freshness guarantee is ≤ 60 minutes. Webhook-triggered immediate sync is available but requires the Git host to be able to reach the application's public URL.

## Rationale

The user listed both post-receive hooks and GitHub/GitLab webhooks as options without committing to either. Polling works universally (self-hosted, GitHub, GitLab, Bitbucket, any HTTPS-accessible repo) with zero additional infrastructure. Webhooks provide near-real-time updates but require network reachability from the Git host to the dashboard — not guaranteed in all deployment scenarios.

## Alternatives Considered

- **Webhooks only**: Simpler code path, real-time, but breaks for repos behind firewalls or without public URLs.
- **Polling only (no webhook)**: Chosen as default; webhooks added as a fast-path on top.

## Notes

- The 60-minute default is configurable via `SYNC_INTERVAL_MINUTES` environment variable.
- In environments where the app is unreachable from the Git host, users should set a shorter polling interval instead of relying on webhooks.
- Fork discovery (ASSUMPTION-008) already requires a platform API token; polling can use that same token to check for new forks on each cycle.

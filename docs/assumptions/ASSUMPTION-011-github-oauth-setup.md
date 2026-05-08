---
ID: ASSUMPTION-011
Category: Security
Status: open
Created: 2026-05-08
Context: FR-07 Authentication — GitHub OAuth
Level: 1
---

# GitHub OAuth requires a registered OAuth App and the read:org scope

## Assumption

Authentication (FR-07) is implemented using a GitHub OAuth App (not a GitHub App). The OAuth flow requests the `read:org` scope so the application can verify org membership after login. The following configuration values are required at deploy time:

- `GITHUB_CLIENT_ID` — from the registered OAuth App
- `GITHUB_CLIENT_SECRET` — from the registered OAuth App
- `GITHUB_ORG` — the organisation name whose members are granted access
- `SECRET_KEY` — used to sign the session cookie

## Rationale

GitHub OAuth Apps are the simplest way to delegate identity to GitHub. The `read:org` scope is the minimum required to call `GET /orgs/{org}/members/{username}`. Using Authlib in FastAPI keeps the implementation concise; session state is stored in a signed cookie (no server-side session store needed at this scale).

## Important caveat: org membership visibility

`GET /orgs/{org}/members/{username}` only returns a result if:
- The org's membership is public, **or**
- The authenticated user has explicitly made their membership public, **or**
- The OAuth App has been granted access by a org owner (third-party app restrictions)

If the org has third-party application restrictions enabled, an org owner must approve the OAuth App before members can use it. This is a GitHub settings step, not a code issue.

## Alternatives Considered

- **GitHub App**: More powerful, but heavier setup (private key, installation flow) — overkill for read-only org membership checks.
- **oauth2-proxy**: Moves auth outside the app entirely, easier to swap providers, but adds an extra container and complicates the single-container deployment goal (QR-03).

## Notes

- The callback URL registered in the OAuth App must match `GITHUB_CALLBACK_URL` in config exactly, including scheme and port.
- For local development, GitHub allows `http://localhost` as a callback URL in OAuth Apps.
- Session cookie is HTTP-only and secure (in production); `SECRET_KEY` must be a strong random value.

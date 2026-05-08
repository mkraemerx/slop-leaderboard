"""GitHub push webhook handler (FR-08).

The endpoint lives at /webhooks/github. It is exempt from session auth
(see app/auth.py PUBLIC_PATHS); when a webhook secret is configured, every
request must carry a valid `X-Hub-Signature-256` HMAC or it is rejected
without enqueueing any work (QR-05).
"""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, HTTPException, Request, Response

from . import jobs


router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """GitHub sends the HMAC-SHA256 in the form `sha256=<hex>`.

    Constant-time comparison via `hmac.compare_digest` so timing differences
    don't leak the true signature.
    """
    if not header or not header.startswith("sha256="):
        return False
    sent = header[len("sha256="):]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, expected)


@router.post("/github")
async def github_webhook(request: Request) -> Response:
    cfg = request.app.state.config
    body = await request.body()
    secret = cfg.github_webhook_secret
    sig = request.headers.get("X-Hub-Signature-256")

    if secret:
        if not verify_signature(secret, body, sig):
            raise HTTPException(status_code=401, detail="invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "push":
        # Other events (ping, etc.) are accepted but not actioned. Returning
        # 204 lets GitHub's delivery dashboard show the request as healthy.
        return Response(status_code=204)

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    repo_url = (
        payload.get("repository", {}).get("html_url")
        or payload.get("repository", {}).get("clone_url")
    )
    if not repo_url:
        raise HTTPException(status_code=400, detail="missing repository.html_url")
    repo_url = repo_url.rstrip("/")
    if repo_url.endswith(".git"):
        repo_url = repo_url[:-4]

    conn = request.app.state.db
    row = conn.execute(
        "SELECT id FROM forks WHERE LOWER(url) = LOWER(?) LIMIT 1",
        (repo_url,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="fork not tracked")

    jobs.enqueue_analysis(conn, int(row["id"]), kind="sync")
    return Response(status_code=202)

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    db_path: Path
    repos_dir: Path

    github_token: str | None
    root_repo_url: str | None

    github_client_id: str | None
    github_client_secret: str | None
    github_callback_url: str | None
    github_org: str | None

    secret_key: str | None
    sync_interval_minutes: int

    github_webhook_secret: str | None


def load_config(env: dict[str, str] | None = None) -> Config:
    src = env if env is not None else os.environ
    data_dir = Path(src.get("DATA_DIR", "./data")).resolve()
    return Config(
        data_dir=data_dir,
        db_path=data_dir / "db.sqlite3",
        repos_dir=data_dir / "repos",
        github_token=src.get("GITHUB_TOKEN") or None,
        root_repo_url=src.get("ROOT_REPO_URL") or None,
        github_client_id=src.get("GITHUB_CLIENT_ID") or None,
        github_client_secret=src.get("GITHUB_CLIENT_SECRET") or None,
        github_callback_url=src.get("GITHUB_CALLBACK_URL") or None,
        github_org=src.get("GITHUB_ORG") or None,
        secret_key=src.get("SECRET_KEY") or None,
        sync_interval_minutes=int(src.get("SYNC_INTERVAL_MINUTES", "60")),
        github_webhook_secret=src.get("GITHUB_WEBHOOK_SECRET") or None,
    )

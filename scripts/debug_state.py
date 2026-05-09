"""Quick dump of DB state — root repo + tracked forks.

Run: `uv run --env-file .env python scripts/debug_state.py`
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config  # noqa: E402
from app.db import connect, init_schema  # noqa: E402
from app.repos import get_root_repo, list_forks  # noqa: E402


def main() -> None:
    cfg = load_config()
    conn = connect(cfg.db_path)
    init_schema(conn)

    root = get_root_repo(conn)
    print(f"root: {root}")
    print()
    forks = list_forks(conn)
    if not forks:
        print("forks: (none)")
        return
    print(f"forks ({len(forks)}):")
    for f in forks:
        print(f"  - owner={f.owner!r} name={f.name!r} via={f.discovered_via} "
              f"status={f.sync_status} url={f.url}")


if __name__ == "__main__":
    main()

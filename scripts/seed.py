"""One-shot seed helper: set (or replace) the root repo and add manual forks.

Run:
  uv run --env-file .env python scripts/seed.py \
      --root https://github.com/<org>/<root-repo> \
      --fork https://github.com/<owner1>/<repo> \
      --fork https://github.com/<owner2>/<repo>
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_config  # noqa: E402
from app.db import connect, init_schema  # noqa: E402
from app.repos import add_fork_manual, get_root_repo, set_root_repo  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True,
                    help="root repo URL, e.g. https://github.com/org/root")
    ap.add_argument("--fork", action="append", default=[],
                    help="fork URL to add manually (repeatable)")
    args = ap.parse_args()

    cfg = load_config()
    conn = connect(cfg.db_path)
    init_schema(conn)

    root = set_root_repo(conn, args.root)
    print(f"root set: {root.owner}/{root.name} ({root.url})")

    for url in args.fork:
        try:
            f = add_fork_manual(conn, url)
            print(f"fork added: {f.owner}/{f.name}")
        except sqlite3.IntegrityError:
            print(f"fork already tracked: {url}")
        except Exception as exc:
            print(f"fork failed: {url}: {exc}")

    assert get_root_repo(conn) is not None


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""
Embedded PostgreSQL + pgvector launcher (no Docker required).

Uses the `pgserver` pip package (self-contained PostgreSQL 16 binaries) and
compiles the pgvector extension from source into that install. Requires a
Linux x86_64 host with git + a C toolchain (gcc/make) — GitHub Codespaces,
cloud workspaces and devcontainers all qualify.

Usage:
    python scripts/start_db.py                 # start server + create DB, print URL
    python scripts/start_db.py --prepare-only  # install binaries + compile pgvector only
    python scripts/start_db.py --workdir DIR --db NAME

Prints a DATABASE_URL using a unix socket, e.g.
    postgresql://postgres:@/aitools?host=/abs/workdir
(config/settings.py understands the ?host= form).
"""
import argparse
import subprocess
import shutil
import sys
import pathlib


def run(cmd, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def ensure_pgserver():
    try:
        import pgserver  # noqa: F401

        return
    except ImportError:
        print("• Installing embedded PostgreSQL (pgserver wheel, ~50 MB)…")
        run([sys.executable, "-m", "pip", "install", "--quiet", "pgserver"])


def pg_install_dir() -> pathlib.Path:
    import pgserver

    return pathlib.Path(pgserver.__file__).parent / "pginstall"


def pgvector_installed(pginstall: pathlib.Path) -> bool:
    ext_dir = pginstall / "share" / "postgresql" / "extension"
    return ext_dir.exists() and any(ext_dir.glob("vector--*.sql"))


def ensure_pgvector(pginstall: pathlib.Path):
    """Compile + install pgvector into the pgserver tree (once)."""
    if pgvector_installed(pginstall):
        return

    print("• Compiling pgvector into the embedded PostgreSQL (one-time)…")
    for tool in ("git", "gcc", "make"):
        if shutil.which(tool) is None:
            raise SystemExit(
                f"ERROR: '{tool}' is required to build pgvector. Install build tools "
                "(e.g. `sudo apt-get install -y build-essential git`) and re-run."
            )

    tmp = pathlib.Path("/tmp/pgvector-build")
    if not (tmp / "Makefile").exists():
        tmp.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", "--depth", "1", "--branch", "v0.7.2",
             "https://github.com/pgvector/pgvector.git", str(tmp)])
    pg_config = pginstall / "bin" / "pg_config"
    run(["make", f"PG_CONFIG={pg_config}", "-C", str(tmp), "-s"])
    run(["make", f"PG_CONFIG={pg_config}", "-C", str(tmp), "install"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default=".pgdata", help="server data directory")
    parser.add_argument("--db", default="aitools", help="database name to create")
    parser.add_argument("--prepare-only", action="store_true",
                        help="install binaries + pgvector, do not start a server")
    args = parser.parse_args()

    ensure_pgserver()
    pginstall = pg_install_dir()
    ensure_pgvector(pginstall)
    if args.prepare_only:
        print("✓ Embedded PostgreSQL + pgvector ready.")
        return

    import pgserver

    workdir = pathlib.Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    # cleanup_mode=None -> keep the server running after this script exits
    # (default 'stop' would tear it down with the launching process).
    server = pgserver.get_server(str(workdir), cleanup_mode=None)

    exists = server.psql(
        f"SELECT 1 FROM pg_database WHERE datname = '{args.db}';"
    ).strip()
    if "1" not in exists:
        print(f"• Creating database {args.db}…")
        server.psql(f"CREATE DATABASE {args.db};")

    # Socket lives in the workdir; Django reads it from the ?host= query param.
    # NB: empty host + /dbname before the query string (dj-database-url format).
    url = f"postgresql://postgres:@/{args.db}?host={workdir}"
    print(url)


if __name__ == "__main__":
    main()

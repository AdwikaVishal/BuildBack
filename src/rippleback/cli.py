"""RippleBack command-line interface.

Entry points (declared in ``pyproject.toml``):

* ``rippleback up``            — migrate the DB, then run the MCP server and the
                                 adapter (optionally serving a built frontend).
* ``rippleback doctor``        — print integration status and DB connectivity.
* ``rippleback seed-sample``   — load the ``samples/`` plan/incidents/catalog.
* ``rippleback sync``          — run one PM sync + incident ingest + outbox drain.
* ``rippleback import-substitutes --file X.yaml``
* ``rippleback version``

Every command uses the same environment configuration as the servers; no
command requires arguments to work with an empty ``.env``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from rippleback import __version__


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_doctor(_args: argparse.Namespace) -> int:
    from rippleback.config import integration_status, load_settings
    from rippleback.db import get_engine, run_migrations, session_scope
    from sqlalchemy import text

    settings = load_settings()
    report: dict[str, object] = {"version": __version__, "settings": {}, "checks": {}}
    status = integration_status()
    report["settings"] = status
    ok = True
    engine = None
    try:
        engine = get_engine()
        applied = run_migrations(engine)
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        report["checks"]["database"] = {
            "ok": True,
            "url_scheme": engine.url.get_backend_name(),
            "migrations_applied": applied,
        }
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        ok = False
        report["checks"]["database"] = {"ok": False, "error": str(exc)}
    report["checks"]["writes"] = {
        "ok": True,
        "enabled": settings.writes_enabled,
        "api_key_required": settings.api_key_required,
    }
    if settings.adapter_bound_public and not settings.api_key_required:
        report["checks"]["writes"]["warning"] = (
            "adapter host is not loopback and RIPPLEBACK_API_KEY is unset; "
            "writes will be refused"
        )
    report["ok"] = ok
    _print(report)
    return 0 if ok else 1


def cmd_seed(args: argparse.Namespace) -> int:
    from rippleback.db import init_db
    from rippleback.seeder import SeedError, seed_sample

    init_db()
    try:
        summary = seed_sample(args.directory)
    except SeedError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, **summary})
    return 0


def cmd_sync(_args: argparse.Namespace) -> int:
    from rippleback.db import init_db
    from rippleback.integrations import outbox
    from rippleback.sync import sync_all

    init_db()
    result = sync_all()
    result["outbox"] = outbox.process_outbox()
    _print(result)
    return 0 if result.get("ok") else 1


def cmd_import_substitutes(args: argparse.Namespace) -> int:
    from rippleback.db import init_db
    from rippleback.seeder import SeedError, import_substitute_file

    init_db()
    try:
        counts = import_substitute_file(args.file, source="cli")
    except SeedError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    _print({"ok": True, "catalog_rows": counts})
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    _print({"version": __version__})
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    """Run the MCP server and the adapter together (backend production mode)."""
    from rippleback.config import load_settings
    from rippleback.db import init_db

    settings = load_settings()
    init_db()
    report = {"adapter_host": settings.adapter_host, "writes_enabled": settings.writes_enabled}
    if settings.adapter_bound_public and not settings.api_key_required:
        report["warning"] = (
            "adapter binds a non-loopback host without RIPPLEBACK_API_KEY; "
            "writes are refused"
        )
    _print(report)

    children: list[subprocess.Popen] = []
    stop = threading.Event()

    def _run(module: str) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, "-m", module], env=os.environ.copy())

    try:
        if not args.no_server:
            children.append(_run("rippleback.server"))
        if not args.no_adapter:
            children.append(_run("rippleback.adapter"))
        if not children:
            return 0
        for child in children:
            child.wait()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        stop.set()
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rippleback", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="run the MCP server and adapter together")
    up.add_argument("--no-server", action="store_true", help="skip the MCP server")
    up.add_argument("--no-adapter", action="store_true", help="skip the adapter")
    up.set_defaults(func=cmd_up)

    doctor = sub.add_parser("doctor", help="diagnose configuration and database")
    doctor.set_defaults(func=cmd_doctor)

    seed = sub.add_parser("seed-sample", help="load the samples/ directory")
    seed.add_argument(
        "directory", nargs="?", default=str(Path("samples")), help="sample directory"
    )
    seed.set_defaults(func=cmd_seed)

    sync = sub.add_parser("sync", help="run one sync + incident ingest + outbox drain")
    sync.set_defaults(func=cmd_sync)

    subs = sub.add_parser("import-substitutes", help="import a substitute YAML file")
    subs.add_argument("--file", required=True)
    subs.set_defaults(func=cmd_import_substitutes)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


def doctor_main() -> int:
    return cmd_doctor(argparse.Namespace())


def seed_main() -> int:
    return cmd_seed(argparse.Namespace(directory=str(Path("samples"))))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

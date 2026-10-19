"""Command-line interface: version, doctor, seed-sample, up, import-substitutes.

Commands are invoked in-process via ``cli.main(argv)`` with output captured —
no subprocesses, no servers.
"""

from __future__ import annotations

import json

import pytest

from rippleback import cli


def _last_json(capsys: pytest.CaptureFixture[str]) -> dict:
    out = capsys.readouterr().out
    payloads = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(out):
        if out[index] in " \t\r\n":
            index += 1
            continue
        value, end = decoder.raw_decode(out, index)
        payloads.append(value)
        index = end
    assert payloads, f"no JSON printed; stdout was: {out!r}"
    return payloads[-1]


class TestVersion:
    def test_version_prints_the_package_version(self, capsys) -> None:
        from rippleback import __version__

        assert cli.main(["version"]) == 0
        assert _last_json(capsys) == {"version": __version__}


class TestDoctor:
    def test_doctor_reports_a_healthy_database(self, capsys) -> None:
        code = cli.main(["doctor"])
        report = _last_json(capsys)
        assert code == 0
        assert report["ok"] is True
        assert report["checks"]["database"]["ok"] is True
        assert report["checks"]["database"]["url_scheme"] == "sqlite"
        assert "pm_provider" in report["settings"]

    def test_doctor_never_leaks_secret_values(self, capsys, monkeypatch) -> None:
        monkeypatch.setenv("JIRA_API_TOKEN", "sk-live-doctor-secret")
        cli.main(["doctor"])
        captured = capsys.readouterr().out
        assert "sk-live-doctor-secret" not in captured


class TestSeedSample:
    def _write_sample(self, tmp_path, sid: str):
        plans = tmp_path / "plans"
        plans.mkdir()
        (plans / "plan.yaml").write_text(
            f"""\
scenario_id: {sid}
plan_id: plan-{sid}
version: 1
status: healthy
entities:
  - id: one
    name: One
    status: pending
  - id: two
    name: Two
    status: pending
dependencies:
  - dependent: two
    prerequisite: one
constraints: []
""",
            encoding="utf-8",
        )
        (tmp_path / "incidents.yaml").write_text(
            f"""\
- scenario_id: {sid}
  entity_id: one
  severity: high
  occurred_at: "2026-01-01T00:00:00+00:00"
  external_id: inc-1
""",
            encoding="utf-8",
        )
        return tmp_path

    def test_seed_sample_imports_plans_and_incidents(self, tmp_path, capsys) -> None:
        import uuid

        sid = f"cli-{uuid.uuid4().hex[:8]}"
        directory = self._write_sample(tmp_path, sid)
        assert cli.main(["seed-sample", str(directory)]) == 0
        summary = _last_json(capsys)
        assert summary["ok"] is True
        assert summary["plans"][0]["scenario_id"] == sid
        assert summary["incidents"] == 1

    def test_missing_sample_directory_reports_an_error(self, tmp_path, capsys) -> None:
        code = cli.main(["seed-sample", str(tmp_path / "nope")])
        assert code == 1
        payload = _last_json(capsys)
        assert payload["ok"] is False

    def test_seed_main_entry_point_uses_the_samples_dir(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(
            "rippleback.seeder.seed_sample",
            lambda directory: {"plans": [], "substitutes": {}, "incidents": 0},
        )
        code = cli.seed_main()
        payload = _last_json(capsys)
        assert code == 0
        assert payload["ok"] is True


class TestImportSubstitutes:
    def test_missing_file_returns_one(self, tmp_path, capsys) -> None:
        code = cli.main(["import-substitutes", "--file", str(tmp_path / "absent.yaml")])
        assert code == 1
        assert _last_json(capsys)["ok"] is False

    def test_valid_file_imports(self, tmp_path, capsys, monkeypatch) -> None:
        from rippleback import repository
        from rippleback.db import session_scope

        import uuid

        sid = f"cli-{uuid.uuid4().hex[:8]}"
        scenario = {
            "scenario_id": sid,
            "plan_id": f"plan-{sid}",
            "version": 1,
            "status": "healthy",
            "entities": [
                {"id": "x", "name": "X", "status": "pending"},
                {"id": "y", "name": "Y", "status": "pending", "assignee": "r1"},
            ],
            "resources": [{"id": "r1", "name": "R1", "capacity": 1}],
            "dependencies": [],
            "constraints": [],
        }
        with session_scope() as session:
            repository.save_version(session, scenario, origin="test", version=1, source="test")

        path = tmp_path / "subs.yaml"
        path.write_text(f"{sid}:\n  x:\n    - y\n", encoding="utf-8")
        assert cli.main(["import-substitutes", "--file", str(path)]) == 0
        assert _last_json(capsys)["catalog_rows"][sid] == 1


class TestUp:
    def test_up_with_nothing_to_run_just_reports(self, capsys) -> None:
        assert cli.main(["up", "--no-server", "--no-adapter"]) == 0
        report = _last_json(capsys)
        assert "adapter_host" in report
        assert "writes_enabled" in report

    def test_unknown_command_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["nonsense"])


class TestParser:
    def test_all_documented_commands_are_wired(self) -> None:
        parser = cli.build_parser()
        invocations = [
            ["up"],
            ["doctor"],
            ["seed-sample"],
            ["sync"],
            ["import-substitutes", "--file", "x.yaml"],
            ["version"],
        ]
        for argv in invocations:
            assert parser.parse_args(argv).func is not None

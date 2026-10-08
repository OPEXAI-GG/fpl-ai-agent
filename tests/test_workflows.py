"""The GitHub Actions workflows are code too: they must parse, run on the intended schedule, stay
in dry mode, keep secrets out of the file and never run unattended steps with write powers they do
not need."""

from pathlib import Path

import pytest
import yaml

WF = Path(__file__).parent.parent / ".github" / "workflows"
FILES = sorted(WF.glob("*.yml"))


def load(path):
    data = yaml.safe_load(path.read_text())
    data["on"] = data.pop(True, data.get("on"))  # YAML reads a bare `on:` as the boolean True
    return data


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_workflows_parse_and_have_jobs(path):
    wf = load(path)
    assert wf["jobs"] and wf["on"] and wf["permissions"]
    for job in wf["jobs"].values():
        assert job["steps"] and job["runs-on"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_literal_secrets_or_live_mode(path):
    text = path.read_text()
    for needle in ("sk-ant", "ghp_", "github_pat_", "AGENT_MODE: live", "AGENT_MODE=live"):
        assert needle not in text
    for line in text.splitlines():
        if "secrets." in line:
            assert "${{ secrets." in line  # only ever referenced, never inlined


def test_data_workflow_schedule_and_publish_only_after_update():
    wf = load(WF / "data.yml")
    crons = [s["cron"] for s in wf["on"]["schedule"]]
    assert "0 9 * * 2" in crons  # Tuesday 09:00 UTC, after the gameweek scores lock
    steps = [s.get("name") for s in wf["jobs"]["update"]["steps"]]
    assert steps.index("Update database") < steps.index("Publish database")
    assert wf["permissions"]["contents"] == "write"
    assert wf["concurrency"]["cancel-in-progress"] is False  # never kill a run mid-update


def test_ci_has_the_coverage_gate():
    ci = (WF / "ci.yml").read_text()
    assert "--cov-fail-under" in ci and "ruff check" in ci


def test_helper_scripts_are_executable_and_strict():
    for name in ("data_restore.sh", "data_publish.sh"):
        path = WF.parent.parent / "scripts" / name
        assert path.stat().st_mode & 0o111
        assert "set -" in path.read_text()

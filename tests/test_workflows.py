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


def test_plan_workflow_defaults_to_dry_and_publishes_after_planning():
    wf = load(WF / "plan.yml")
    job = wf["jobs"]["plan"]
    mode = job["env"]["AGENT_MODE"]
    assert "vars.AGENT_MODE" in mode and "'dry'" in mode  # one variable, dry when unset
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Plan") < names.index("Publish database")
    assert "AGENT_MODE: live" not in (WF / "plan.yml").read_text()  # never hard-coded live
    group = wf["concurrency"]["group"]  # serialised with the data updates; a dry check apart
    assert group.endswith("|| 'data' }}") and "inputs.dry == 'true'" in group
    publish = next(s for s in job["steps"] if s.get("name") == "Publish database")
    assert "inputs.dry != 'true'" in publish["if"]  # a dry check never publishes


def test_token_check_keeps_clear_of_plan_runs():
    wf = load(WF / "token-check.yml")
    assert wf["concurrency"]["group"] == "fpl-token"
    assert wf["permissions"]["contents"] == "read"
    assert "plan.yml/runs?status=in_progress" in (WF / "token-check.yml").read_text()


def test_review_workflow_runs_after_the_data_update():
    wf = load(WF / "review.yml")
    assert wf["on"]["schedule"][0]["cron"] == "30 10 * * 2"
    text = (WF / "review.yml").read_text()
    assert "review --db" in text and "--export" in text


def test_backtest_workflow_is_read_only_and_manual():
    wf = load(WF / "backtest.yml")
    assert wf["permissions"] == {"contents": "read"}
    assert "workflow_dispatch" in wf["on"] and "schedule" not in wf["on"]

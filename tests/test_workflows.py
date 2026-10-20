"""The GitHub Actions workflows are code too: they must parse, run on the intended schedule, stay
in dry mode, keep secrets out of the file and never run unattended steps with write powers they do
not need."""

from pathlib import Path

import pytest
import yaml

WF = Path(__file__).parent.parent / ".github" / "workflows"
FILES = sorted(WF.glob("*.yml"))


def load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
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
    text = path.read_text(encoding="utf-8")
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
    ci = (WF / "ci.yml").read_text(encoding="utf-8")
    assert "--cov-fail-under" in ci and "ruff check" in ci


def _git_mode(path: Path) -> str | None:
    """The file mode Git records (what Linux checkouts get); None without a Git checkout."""
    import shutil
    import subprocess

    if not shutil.which("git"):
        return None
    out = subprocess.run(["git", "ls-files", "-s", path.name], cwd=path.parent,
                         capture_output=True, text=True, check=False).stdout  # fmt: skip
    return out.split()[0] if out else None


def test_helper_scripts_are_executable_and_strict():
    import os

    for name in ("data_restore.sh", "data_publish.sh", "plan_publish.sh"):
        path = WF.parent.parent / "scripts" / name
        if os.name == "posix":
            assert path.stat().st_mode & 0o111
        else:  # Windows has no exec bit: check the mode Git will give a Linux checkout
            mode = _git_mode(path)
            if mode is None:
                pytest.skip("no exec bit on this filesystem and no Git index to read")
            assert mode == "100755", (name, mode)
        assert "set -" in path.read_text(encoding="utf-8")


def test_plan_workflow_defaults_to_dry_and_publishes_after_planning():
    wf = load(WF / "plan.yml")
    job = wf["jobs"]["plan"]
    mode = job["env"]["AGENT_MODE"]
    assert "vars.AGENT_MODE" in mode and "'dry'" in mode  # one variable, dry when unset
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Plan") < names.index("Publish database")
    assert "AGENT_MODE: live" not in (WF / "plan.yml").read_text(
        encoding="utf-8"
    )  # never hard-coded live
    group = wf["concurrency"]["group"]  # serialised with the data updates; a dry check apart
    assert group.endswith("|| 'data' }}") and "inputs.dry == 'true'" in group
    publish = next(s for s in job["steps"] if s.get("name") == "Publish database")
    assert "inputs.dry != 'true'" in publish["if"]  # a dry check never publishes


def test_token_check_keeps_clear_of_plan_runs():
    wf = load(WF / "token-check.yml")
    assert wf["concurrency"]["group"] == "fpl-token"
    assert wf["permissions"]["contents"] == "read"
    assert "plan.yml/runs?status=in_progress" in (WF / "token-check.yml").read_text(
        encoding="utf-8"
    )


def test_auth_jobs_share_a_lease_and_check_token_freshness_after_acquisition():
    plan = load(WF / "plan.yml")["jobs"]["plan"]
    check = load(WF / "token-check.yml")["jobs"]["check"]
    assert "'fpl-auth'" in plan["concurrency"]["group"]
    assert check["concurrency"]["group"] == "fpl-auth"
    for job in (plan, check):
        assert job["concurrency"]["cancel-in-progress"] is False
        assert not any(
            k in job.get("env", {})
            for k in ("FPL_REFRESH_TOKEN", "ANTHROPIC_API_KEY", "ODDS_API_KEY", "NOTIFY_WEBHOOK")
        )
        auth_steps = [s for s in job["steps"] if "FPL_REFRESH_TOKEN" in s.get("env", {})]
        assert len(auth_steps) == 1
        assert auth_steps[0]["env"]["FPL_AUTH_FRESHNESS_REQUIRED"] == "true"
    # Backfill holds only the data lease, never the auth lease.
    assert "fpl-auth" not in (WF / "backfill.yml").read_text(encoding="utf-8")


def test_review_workflow_runs_after_the_data_update():
    wf = load(WF / "review.yml")
    assert wf["on"]["schedule"][0]["cron"] == "30 10 * * 2"
    text = (WF / "review.yml").read_text(encoding="utf-8")
    assert "review --db" in text and "--export" in text


def test_backtest_workflow_is_read_only_and_manual():
    wf = load(WF / "backtest.yml")
    assert wf["permissions"] == {"contents": "read"}
    assert "workflow_dispatch" in wf["on"] and "schedule" not in wf["on"]


def test_backtest_workflow_can_replay_a_season():
    wf = load(WF / "backtest.yml")
    assert wf["on"]["workflow_dispatch"]["inputs"]["mode"]["options"] == [
        "projections",
        "replay",
        "retro",
    ]
    steps = {s.get("name"): s for s in wf["jobs"]["backtest"]["steps"]}
    assert "mode == 'replay'" in steps["Season replay"]["if"]
    assert "fpl-agent replay" in steps["Season replay"]["run"]
    assert '--set "$s"' in steps["Season replay"]["run"]
    assert "settings" in steps["Season replay"]["env"]["SETTINGS"]
    assert "mode == 'projections'" in steps["Back-test"]["if"]
    assert "fpl-agent retro" in steps["Gameweek retro"]["run"]
    assert "mode == 'retro'" in steps["Gameweek retro"]["if"]


def test_review_workflow_sends_the_gameweek_summary_without_blocking_publish():
    wf = load(WF / "review.yml")
    steps = wf["jobs"]["review"]["steps"]
    names = [s.get("name") for s in steps]
    summary = steps[names.index("Gameweek summary to Discord")]
    assert summary["continue-on-error"] is True and "gw-summary --notify" in summary["run"]
    assert names.index("Gameweek summary to Discord") < names.index("Publish database")


def test_discord_bot_workflows_reply_with_a_masked_token_and_never_write():
    wf = load(WF / "discord.yml")
    assert wf["permissions"] == {"contents": "read"}
    steps = wf["jobs"]["explain"]["steps"]
    assert "add-mask" in steps[0]["run"]  # hidden before anything else logs it
    assert load(WF / "discord-deploy.yml")["on"] == {"workflow_dispatch": None}  # by hand only
    plan = load(WF / "plan.yml")
    names = [s.get("name") for s in plan["jobs"]["plan"]["steps"]]
    assert names[0] == "Hide the Discord reply token"
    assert names.index("Plan") < names.index("Publish the plan for the Discord bot")
    assert names[-1] == "Reply in Discord"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_manual_inputs_never_become_shell_code(path):
    """A workflow input pasted into a `run:` script could execute as code; inputs reach the
    shell only through `env:` (and are validated there)."""
    for job in load(path)["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run", "")
            assert "inputs." not in script, (path.name, step.get("name"), "use env: instead")


def test_the_scoring_rules_ship_inside_the_wheel():
    import tomllib

    cfg = tomllib.loads((WF.parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    inc = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert inc["rules"] == "fpl_agent/rules/data"
    assert any((WF.parent.parent / "rules").glob("*.yaml"))


def test_browser_ci_uses_the_local_dashboard_and_installs_python():
    steps = load(WF / "playwright.yml")["jobs"]["test"]["steps"]
    scripts = [s.get("run", "") for s in steps]
    assert "uv sync --frozen" in scripts
    assert "npm run test:dashboard" in scripts
    assert any("install --with-deps chromium" in s for s in scripts)

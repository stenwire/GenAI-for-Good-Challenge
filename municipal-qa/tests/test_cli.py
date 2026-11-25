import typer
from typer.testing import CliRunner

from municipal_qa.cli import app

PLAN_COMMANDS = [
    "setup-data",
    "profile",
    "index",
    "check-model",
    "run",
    "evaluate",
    "submit",
    "validate",
    "show",
]

runner = CliRunner()


def test_help_lists_every_plan_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in PLAN_COMMANDS:
        assert name in result.output, name


def test_registered_commands_are_exactly_the_plan_commands():
    names = set(typer.main.get_command(app).commands)
    assert names == set(PLAN_COMMANDS)


def test_run_accepts_plan_options():
    result = runner.invoke(app, ["run", "--help"])
    for opt in ["--split", "--limit", "--ids", "--resume"]:
        assert opt in result.output

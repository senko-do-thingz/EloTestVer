"""System-wide module, command registry, and documentation smoke tests."""
from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from utils.command_registry import (
    COMMAND_HELP, admin_command_names, all_command_names, lookup_command_help,
    private_command_names, public_command_names,
)

ROOT = Path(__file__).resolve().parents[1]
CODE_ROOTS = ("cogs", "db", "utils")


def source_paths():
    return [
        p for folder in CODE_ROOTS
        for p in sorted((ROOT / folder).rglob("*.py"))
    ] + [ROOT / "config.py", ROOT / "elobot.py"]


def module_name(path):
    return ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")


@pytest.mark.parametrize("source", source_paths(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_python_module_imports_without_live_discord(source):
    importlib.import_module(module_name(source))


@pytest.mark.parametrize("source", source_paths(), ids=lambda p: str(p.relative_to(ROOT)))
def test_all_source_files_have_valid_ast(source):
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


@pytest.mark.parametrize(
    "source", sorted((ROOT / "cogs").rglob("*.py")),
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_every_extension_setup_is_async_if_present(source):
    module = importlib.import_module(module_name(source))
    setup = getattr(module, "setup", None)
    if setup is not None:
        assert inspect.iscoroutinefunction(setup)


@pytest.mark.parametrize("name,entry", list(COMMAND_HELP.items()))
def test_command_help_entries_are_complete(name, entry):
    assert name == entry.name
    assert entry.description.strip() and entry.syntax.strip() and entry.examples
    assert lookup_command_help(name) is entry
    assert lookup_command_help("-" + name) is entry
    assert lookup_command_help("/" + name) is entry


@pytest.mark.parametrize("entry", list(COMMAND_HELP.values()), ids=lambda e: e.name)
def test_command_help_aliases_resolve(entry):
    for alias in entry.aliases:
        assert lookup_command_help(alias) is entry


def test_command_name_indexes():
    everything = set(all_command_names())
    assert everything == set(COMMAND_HELP) | {
        alias for entry in COMMAND_HELP.values() for alias in entry.aliases
    }
    assert set(public_command_names()).issubset(everything)
    assert set(admin_command_names()).issubset(everything)
    assert set(private_command_names()).issubset(everything)


@pytest.mark.parametrize("name", [
    "register", "rename", "report", "history", "leaderboard", "lbs",
    "stats", "compare", "get_lb", "decay", "c_reg", "months",
    "season", "set_e", "counting", "msg", "ad_rp",
])
def test_main_commands_have_registry_entries(name):
    assert lookup_command_help(name) is not None


@pytest.mark.parametrize("legacy", ["c_registration", "d_registration", "qna"])
def test_old_readme_commands_are_not_advertised(legacy):
    assert lookup_command_help(legacy) is None


def test_private_commands_hidden_outside_management_guild():
    from cogs.help import _lookup_command_help_scoped
    from utils.logger import PRIVATE_GUILD_ID

    private = [name for name, entry in COMMAND_HELP.items() if entry.private]
    assert private
    for name in private:
        assert _lookup_command_help_scoped(name, 987654321) is None
        assert _lookup_command_help_scoped(name, PRIVATE_GUILD_ID) is not None


def test_readme_languages_and_plain_style():
    docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.vi.md",
        ROOT / "docs" / "README.ja.md",
    ]
    for path in docs:
        contents = path.read_text(encoding="utf-8")
        assert contents.startswith("# ") and len(contents) > 300
        assert "\u2013" not in contents and "\u2014" not in contents
        assert not any(
            0x1F000 <= ord(char) <= 0x1FAFF or 0x2600 <= ord(char) <= 0x27BF
            for char in contents
        ), str(path)
        assert "README.md" in contents
        assert "README.vi.md" in contents
        assert "README.ja.md" in contents

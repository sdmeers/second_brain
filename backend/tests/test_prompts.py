"""Prompts live in backend/prompts/*.md; every call site must fill every placeholder."""

import ast
from pathlib import Path

import pytest

from backend.prompts import PROMPTS_DIR, _read_template, load_prompt, prompt_names

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _call_sites() -> list[tuple[str, str, set[str]]]:
    """(file, prompt name, keyword names) for every load_prompt("...") call in the backend."""
    sites = []
    for path in BACKEND_DIR.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "load_prompt"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                sites.append((str(path.relative_to(BACKEND_DIR)), node.args[0].value,
                              {kw.arg for kw in node.keywords}))
    return sites


CALL_SITES = _call_sites()


def test_prompt_directory_is_populated():
    assert "router" in prompt_names()
    assert len(prompt_names()) >= 15


@pytest.mark.parametrize("name", prompt_names())
def test_every_prompt_is_used(name):
    assert any(site[1] == name for site in CALL_SITES), f"prompts/{name}.md is never loaded"


@pytest.mark.parametrize("site", CALL_SITES, ids=lambda s: f"{s[0]}:{s[1]}")
def test_call_site_fills_exactly_the_template_variables(site):
    path, name, kwargs = site
    assert (PROMPTS_DIR / f"{name}.md").exists(), f"{path} loads missing prompt {name!r}"
    expected = set(_read_template(name).get_identifiers())
    assert kwargs == expected, f"{path}: {name} expects {sorted(expected)}, got {sorted(kwargs)}"


def test_header_comment_is_not_sent_to_the_model():
    assert "<!--" not in load_prompt("router")
    assert load_prompt("router").startswith("You are")


def test_missing_variable_raises():
    with pytest.raises(KeyError):
        load_prompt("rag_chat")


def test_literal_braces_survive_rendering():
    """Output templates use {Emoji}-style placeholders that must reach the model intact."""
    rendered = load_prompt("brain_node", themes="   - AI", concepts="Agent")
    assert '"{Emoji} {Extract Title}"' in rendered
    assert "   - AI" in rendered

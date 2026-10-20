"""LLM prompts, one Markdown file per prompt in this directory.

Edit the .md files to change what the models are told; restart the service for the
change to take effect. Each file may open with an HTML comment describing where it is
used and which variables it takes -- that comment is not sent to the model.

Variables use string.Template syntax (``${name}``) rather than str.format, so the
literal ``{...}`` placeholders in the output templates need no escaping. Write a literal
dollar sign as ``$$``.
"""

import re
from functools import cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent

_LEADING_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*?\n", re.DOTALL)


@cache
def _read_template(name: str) -> Template:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    text = _LEADING_COMMENT.sub("", text, count=1)
    return Template(text.strip("\n"))


def load_prompt(name: str, **variables) -> str:
    """Render prompts/<name>.md. A missing variable raises KeyError rather than
    sending the model a prompt with a hole in it."""
    return _read_template(name).substitute(**variables)


def prompt_names() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))

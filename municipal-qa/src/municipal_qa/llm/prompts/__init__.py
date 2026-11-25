"""Prompt templates are Markdown files here, loaded by name (plan §6.7)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def render_prompt(name: str, **fields: str) -> str:
    """One pass over the template, so braces inside inserted source text stay literal."""
    return re.sub(r"\{(\w+)\}", lambda m: fields.get(m.group(1), m.group(0)), load_prompt(name))

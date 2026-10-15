"""Prompts as named sections. Each call's sections are measured into the usage meter, so the
size of everything sent to a model is known per section and per step."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from app.observability.usage import count_prompt

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True, slots=True)
class Section:
    heading: str
    body: str

    def render(self) -> str:
        return f"{self.heading}\n{self.body}"


def assemble(role: str, sections: Iterable[Section | None]) -> str:
    """The prompt text, sections separated by a blank line (None entries are left out)."""
    shown = [section for section in sections if section is not None]
    rendered = [section.render() for section in shown]
    sizes = {section.heading: len(text) for section, text in zip(shown, rendered, strict=True)}
    count_prompt(role, sizes)
    logger.debug("Prompt assembled role=%s chars=%s sections=%s", role, sum(sizes.values()), sizes)
    return "\n\n".join(rendered)

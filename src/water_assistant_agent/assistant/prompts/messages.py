"""Prompt message helper (ported verbatim from core-agent)."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PromptMessage:
    role: Literal["system", "user"]
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

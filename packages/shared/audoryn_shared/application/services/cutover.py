from dataclasses import dataclass
from typing import ClassVar, Literal

CutoverStage = Literal[
    "frontend",
    "system",
    "authentication",
    "organizations",
    "workforce",
    "jobs",
    "results",
    "governance",
    "scheduler",
    "runtime",
    "integrations",
]


@dataclass(frozen=True, slots=True)
class CutoverController:
    stage: CutoverStage

    _ORDER: ClassVar[tuple[CutoverStage, ...]] = (
        "frontend",
        "system",
        "authentication",
        "organizations",
        "workforce",
        "jobs",
        "results",
        "governance",
        "scheduler",
        "runtime",
        "integrations",
    )

    def is_authoritative(self, domain: CutoverStage) -> bool:
        return self._ORDER.index(domain) <= self._ORDER.index(self.stage)

    def require_authoritative(self, domain: CutoverStage) -> None:
        if not self.is_authoritative(domain):
            raise RuntimeError(
                f"{domain} is not authoritative at cutover stage {self.stage}."
            )

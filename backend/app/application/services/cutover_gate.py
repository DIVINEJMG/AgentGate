from dataclasses import dataclass

from app.application.services.cutover import CutoverController, CutoverStage


@dataclass(frozen=True, slots=True)
class CutoverReadiness:
    parity_passed: bool
    security_passed: bool
    migration_validated: bool
    rollback_documented: bool
    frontend_verified: bool

    @property
    def ready(self) -> bool:
        return all(
            (
                self.parity_passed,
                self.security_passed,
                self.migration_validated,
                self.rollback_documented,
                self.frontend_verified,
            )
        )


class FinalCutoverGate:
    def __init__(self, readiness: CutoverReadiness) -> None:
        self._readiness = readiness

    def authorize_stage(self, stage: CutoverStage) -> CutoverController:
        if stage in {"runtime", "integrations"} and not self._readiness.ready:
            raise RuntimeError("Final Python cutover gates have not all passed.")
        return CutoverController(stage)

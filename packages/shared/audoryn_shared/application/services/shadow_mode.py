from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    matched: bool
    reference: object
    candidate: object


class ShadowModeService:
    """Compare deterministic/read-only behavior without duplicating external side effects."""

    async def compare(
        self,
        *,
        reference_call: Callable[[], Awaitable[T]],
        candidate_call: Callable[[], Awaitable[T]],
        side_effect: bool = False,
    ) -> ShadowComparison:
        if side_effect:
            raise PermissionError("Shadow mode cannot duplicate external side effects.")

        reference = await reference_call()
        candidate = await candidate_call()
        return ShadowComparison(
            matched=reference == candidate,
            reference=reference,
            candidate=candidate,
        )

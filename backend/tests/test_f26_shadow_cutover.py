import pytest

from app.application.services.cutover import CutoverController
from app.application.services.shadow_mode import ShadowModeService


@pytest.mark.asyncio
async def test_shadow_mode_compares_read_only_behavior() -> None:
    async def reference():
        return {"outcome": "DENY"}

    async def candidate():
        return {"outcome": "DENY"}

    comparison = await ShadowModeService().compare(
        reference_call=reference,
        candidate_call=candidate,
    )
    assert comparison.matched is True


@pytest.mark.asyncio
async def test_shadow_mode_refuses_duplicate_side_effects() -> None:
    async def reference():
        return "reference"

    async def candidate():
        return "candidate"

    with pytest.raises(PermissionError, match="side effects"):
        await ShadowModeService().compare(
            reference_call=reference,
            candidate_call=candidate,
            side_effect=True,
        )


def test_cutover_progresses_in_safe_order() -> None:
    controller = CutoverController("jobs")
    assert controller.is_authoritative("frontend")
    assert controller.is_authoritative("system")
    assert controller.is_authoritative("authentication")
    assert controller.is_authoritative("organizations")
    assert controller.is_authoritative("workforce")
    assert controller.is_authoritative("jobs")
    assert controller.is_authoritative("results") is False
    assert controller.is_authoritative("runtime") is False
    assert controller.is_authoritative("integrations") is False


def test_runtime_cannot_activate_before_runtime_cutover() -> None:
    with pytest.raises(RuntimeError, match="not authoritative"):
        CutoverController("scheduler").require_authoritative("runtime")

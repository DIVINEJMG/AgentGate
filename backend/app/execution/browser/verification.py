from __future__ import annotations

from dataclasses import dataclass

from app.execution.browser.contracts import BrowserLocator


@dataclass(frozen=True, slots=True)
class BrowserVerificationExpectation:
    url_changed_from: str | None = None
    url_equals: str | None = None
    url_contains: str | None = None
    expected_text: str | None = None
    absent_text: str | None = None
    element_appeared: BrowserLocator | None = None
    element_disappeared: BrowserLocator | None = None
    form_locator: BrowserLocator | None = None
    form_value: str | None = None
    page_state_key: str | None = None
    page_state_value: object = None
    download_created: bool | None = None
    success_indicator: str | None = None
    error_indicator: str | None = None

    @classmethod
    def from_mapping(
        cls,
        value: object,
    ) -> BrowserVerificationExpectation | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise TypeError("Browser verify must be an object.")

        supported = {
            "urlChangedFrom",
            "urlEquals",
            "urlContains",
            "expectedText",
            "absentText",
            "elementAppeared",
            "elementDisappeared",
            "formState",
            "pageState",
            "downloadCreated",
            "successIndicator",
            "errorIndicator",
        }
        unknown = set(value) - supported
        if unknown:
            raise ValueError(f"Unsupported Browser verification field: {min(unknown)}.")

        form_state = value.get("formState")
        form_locator = None
        form_value = None
        if form_state is not None:
            if not isinstance(form_state, dict) or "locator" not in form_state:
                raise ValueError("Browser formState requires a locator.")
            form_locator = BrowserLocator.from_mapping(form_state["locator"])
            if "value" in form_state:
                form_value = str(form_state["value"])

        page_state = value.get("pageState")
        page_state_key = None
        page_state_value = None
        if page_state is not None:
            if not isinstance(page_state, dict) or "key" not in page_state:
                raise ValueError("Browser pageState verification requires a key.")
            page_state_key = str(page_state["key"])
            page_state_value = page_state.get("value")

        return cls(
            url_changed_from=(
                str(value["urlChangedFrom"]) if value.get("urlChangedFrom") is not None else None
            ),
            url_equals=(str(value["urlEquals"]) if value.get("urlEquals") is not None else None),
            url_contains=(
                str(value["urlContains"]) if value.get("urlContains") is not None else None
            ),
            expected_text=(
                str(value["expectedText"]) if value.get("expectedText") is not None else None
            ),
            absent_text=(str(value["absentText"]) if value.get("absentText") is not None else None),
            element_appeared=(
                BrowserLocator.from_mapping(value["elementAppeared"])
                if value.get("elementAppeared") is not None
                else None
            ),
            element_disappeared=(
                BrowserLocator.from_mapping(value["elementDisappeared"])
                if value.get("elementDisappeared") is not None
                else None
            ),
            form_locator=form_locator,
            form_value=form_value,
            page_state_key=page_state_key,
            page_state_value=page_state_value,
            download_created=(
                bool(value["downloadCreated"]) if "downloadCreated" in value else None
            ),
            success_indicator=(
                str(value["successIndicator"])
                if value.get("successIndicator") is not None
                else None
            ),
            error_indicator=(
                str(value["errorIndicator"]) if value.get("errorIndicator") is not None else None
            ),
        )

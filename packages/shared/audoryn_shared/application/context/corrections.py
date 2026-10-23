"""The correction composer: a rejected answer becomes plain instructions that name the wrong value
and the valid choices, never the whole rejected answer echoed back."""
from __future__ import annotations

import difflib
import json
import os
from collections.abc import Iterable, Sequence

from jsonschema import ValidationError

from audoryn_shared.application.context.evidence import as_list, as_map

SHOWN_CHOICES = 15
VALUE_CHARS = 120


def _short(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= VALUE_CHARS else text[:VALUE_CHARS] + "…"


def _closest(value: str, choices: list[str]) -> str | None:
    """The most similar choice, compared after the dotted prefix every choice shares (so
    `github.repository.` does not make every scope look alike)."""
    shared = os.path.commonprefix([value, *choices])
    cut = shared.rfind(".") + 1
    tails = {choice[cut:]: choice for choice in choices}
    match = difflib.get_close_matches(value[cut:], list(tails), n=1, cutoff=0.5)
    return tails[match[0]] if match else None


def unknown_value(name: str, value: object, allowed: Sequence[str]) -> str:
    """'`scope` "x" is not allowed here. Closest: "y". Allowed: …'"""
    choices = sorted(dict.fromkeys(allowed))
    closest = _closest(str(value), choices)
    listed = ", ".join(choices[:SHOWN_CHOICES]) + (f" (+{len(choices) - SHOWN_CHOICES} more)" if len(choices) > SHOWN_CHOICES else "")
    return (f"`{name}` {_short(value)!r} is not allowed here."
            + (f" Closest: {closest!r}." if closest else "") + f" Allowed: {listed}.")


def _where(error: ValidationError, root: str) -> str:
    return ".".join([root, *map(str, error.absolute_path)]) if root else ".".join(map(str, error.absolute_path)) or "the answer"


def _alternatives(error: ValidationError, root: str) -> str:
    """An anyOf/oneOf failure, said by the value that matches no alternative, or by the
    combination of values no alternative allows."""
    instance = as_map(error.instance)
    options = [as_map(as_map(option).get("properties")) for option in as_list(error.validator_value)]
    keys = list(dict.fromkeys(key for option in options for key, rule in option.items() if "const" in as_map(rule)))
    if not instance or not keys:
        reasons = dict.fromkeys(_plain(sub, root) for sub in error.context or [])
        return f"`{_where(error, root)}` matches none of the allowed forms." + (f" Nearest: {next(iter(reasons))}" if reasons else "")
    for key in keys:
        allowed = [str(as_map(option[key])["const"]) for option in options if "const" in as_map(option.get(key))]
        if key in instance and str(instance[key]) not in allowed:
            return unknown_value(key, instance[key], allowed)
    pairs = [", ".join(f"{key}={as_map(option[key])['const']}" for key in keys if key in option)
             for option in options if all(key in option for key in keys)]
    given = ", ".join(f"{key}={_short(instance.get(key))}" for key in keys)
    return f"The combination {given} is not allowed. Allowed combinations: " + "; ".join(pairs[:SHOWN_CHOICES]) + "."


def _plain(error: ValidationError, root: str) -> str:
    if error.validator in {"anyOf", "oneOf"}:
        return _alternatives(error, root)
    if error.validator == "enum":
        return unknown_value(_where(error, root), error.instance, [str(choice) for choice in as_list(error.validator_value)])
    if error.validator == "const":
        return f"`{_where(error, root)}` must be {_short(error.validator_value)!r}, not {_short(error.instance)!r}."
    if error.validator == "additionalProperties" and error.validator_value is False:
        accepted = sorted(as_map(as_map(error.schema).get("properties")))
        extra = sorted(set(as_map(error.instance)) - set(accepted))
        return (f"`{_where(error, root)}` has properties that are not accepted: {', '.join(extra)}. "
                f"Accepted: {', '.join(accepted) or 'none'}.")
    if error.validator == "required":
        missing = [str(name) for name in as_list(error.validator_value) if name not in as_map(error.instance)]
        inside = f" in `{_where(error, root)}`" if root or error.absolute_path else ""
        return f"Missing required {', '.join(repr(name) for name in missing)}{inside}."
    return f"`{_where(error, root)}` {_short(error.instance)!r} fails the {error.validator} rule ({_short(error.validator_value)})."


def schema_correction(errors: Iterable[ValidationError], *, root: str = "", limit: int = 8) -> str:
    """One plain line per distinct problem. `root` names the checked value (for example `input`)."""
    lines = list(dict.fromkeys(_plain(error, root) for error in errors))
    return " ".join(lines[:limit])


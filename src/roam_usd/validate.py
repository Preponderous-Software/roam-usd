"""Stage validity checks with OpenUSD's validation framework.

ARCHITECTURE.md's testing strategy names ``UsdUtils.ComplianceChecker``; usd-core
26.x no longer ships it, and its successor is ``pxr.UsdValidation`` (the framework
``usdchecker`` itself runs). Every registered validator is run.
"""

from __future__ import annotations

from pxr import Usd, UsdValidation


def validation_errors(stage_or_path: Usd.Stage | str) -> list[str]:
    """Return one line per validation *error* (warnings and info are ignored)."""
    stage = Usd.Stage.Open(stage_or_path) if isinstance(stage_or_path, str) else stage_or_path
    registry = UsdValidation.ValidationRegistry()
    context = UsdValidation.ValidationContext(registry.GetOrLoadAllValidators())
    errors = context.Validate(stage)
    return [
        f"{e.GetName()}: {e.GetMessage()}"
        for e in errors
        if e.GetType() == UsdValidation.ValidationErrorType.Error
    ]

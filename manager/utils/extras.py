from typing import Any

from pydantic import WrapValidator
from pydantic_core.core_schema import ValidationInfo, ValidatorFunctionWrapHandler


def strtobool(val: str) -> bool:
    """Convert a string representation of truth to true (1) or false (0).

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.
    """
    val = val.lower()
    if val in ('y', 'yes', 't', 'true', 'on', '1'):
        return True
    elif val in ('n', 'no', 'f', 'false', 'off', '0'):
        return False
    else:
        raise ValueError(f"invalid truth value {val!r}")


# noinspection PyPep8Naming
def ValidatorFallback(fallback_value: Any):
    def use_fallback(
        v: Any,
        handler: ValidatorFunctionWrapHandler,
        info: ValidationInfo,
    ) -> Any:
        try:
            return handler(v)
        except ValueError:
            return fallback_value

    return WrapValidator(use_fallback)
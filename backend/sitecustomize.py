"""TEMPORARY CI diagnostic — catch whoever disables loggers.

Imported by the interpreter itself at startup (via PYTHONPATH=.), so it
arms BEFORE pytest plugins, conftest, and app imports. Replaces
`logging.Logger.disabled` with a data descriptor that prints the exact
stack of any False -> True transition. Remove together with the CI
PYTHONPATH=... once test_backend_log_names_target_server's root cause
is fixed for real.
"""

import logging
import traceback


def _get(self: logging.Logger) -> bool:
    return self.__dict__.get("disabled", False)


def _set(self: logging.Logger, value: bool) -> None:
    if value and not self.__dict__.get("disabled", False):
        print(
            f"[logtrace] logger '{self.name}' disabled by:\n"
            + "".join(traceback.format_stack()[-12:]),
            flush=True,
        )
    self.__dict__["disabled"] = value


logging.Logger.disabled = property(_get, _set)
print("[logtrace] armed at interpreter startup", flush=True)

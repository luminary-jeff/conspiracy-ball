"""Keep `python3 ff.py ...` working whatever python3 is first on PATH.

The tool was built and tested on the macOS system Python (3.9, /usr/bin/python3), which has `requests`
and `numpy` installed and does not change under Homebrew upgrades. On 2026-09-26 a Homebrew Python 3.14
appeared first on PATH with no third-party packages and every command failed with ModuleNotFoundError.
If the interpreter running us lacks `requests`, re-launch the same command under the system Python.
"""
import os
import sys

SYSTEM_PYTHON = "/usr/bin/python3"


def ensure_deps():
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass
    if os.path.realpath(sys.executable) != os.path.realpath(SYSTEM_PYTHON) and os.path.exists(SYSTEM_PYTHON):
        os.execv(SYSTEM_PYTHON, [SYSTEM_PYTHON] + sys.argv)
    sys.exit("python3 has no 'requests' module and %s is missing; run: /usr/bin/python3 -m pip install --user requests numpy" % SYSTEM_PYTHON)

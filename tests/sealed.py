"""Stops the tests from running on a live desktop session.

They start processes and send signals. On 8 November 2026 a mocked pid reached
kill(-1) and killed the session running them. scripts/check.py runs them in private
namespaces without the session's variables, and CI runs them on a disposable machine.
"""
import os
import sys

if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get("WAYLAND_DISPLAY"):
    print("Refusing to run on a live desktop session: use python3 scripts/check.py", file=sys.stderr)
    os._exit(2)

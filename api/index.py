"""Vercel entry point: every request is rewritten here (see vercel.json).
Vercel detects functions by a top-level `handler` class, so subclass rather than alias."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from server import Handler  # noqa: E402


class handler(Handler):  # noqa: N801  (name required by Vercel)
    pass

"""Vercel entry point: every request is rewritten here (see vercel.json)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from server import Handler as handler  # noqa: E402,F401

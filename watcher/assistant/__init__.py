"""The chat assistant: guards -> memory -> decomposer -> intent router -> planner -> validator ->
executor -> deterministic summary, with a decision cache and a per-message trace. See pipeline.py."""

from .pipeline import fallback, handle  # noqa: F401

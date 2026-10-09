"""Community Notice -> Action backend package.

This marker matters: the repo also has a root-level `app.py` (the Gradio UI),
and Python prefers a package with `__init__.py` over a same-named module.
Without this file `import app` would resolve to the UI and break
`uvicorn app.main:app`.
"""

__all__ = ["main", "config", "ingest", "ollama_client", "prompts", "schemas", "store"]

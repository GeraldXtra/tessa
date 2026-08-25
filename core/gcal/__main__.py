"""
`python -m core.gcal` — the command Gerald actually runs.

A separate __main__ so the CLI does not re-import `core.gcal.google` after
the package __init__ already imported it, which produces a RuntimeWarning about
the module being present in sys.modules twice.
"""

from .google import _cli

raise SystemExit(_cli())

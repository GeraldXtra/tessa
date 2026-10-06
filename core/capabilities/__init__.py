"""
core/capabilities/ — Session 3's territory. See VAULT-DESIGN.md.

Nothing in this package imports core.brain or core.tools at import time. The
daemon (core/server.py) and the executor (core/brain/executor.py) import from
HERE; this package never imports back. That one-way direction is what keeps the
vault free of a circular import — see VAULT-DESIGN.md §0.3 and the build report.
"""

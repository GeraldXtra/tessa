"""
core/capabilities/vault — the credential vault. Design: ../VAULT-DESIGN.md.

Import direction (VAULT-DESIGN.md §0.3): this package imports ONLY the stdlib.
core/server.py and core/brain/executor.py import from here; nothing here
imports them back. `core.capabilities.vault.tools` is the one module that
touches `core.tools.base`, and it is deliberately not imported here.
"""

from .dpapi import DpapiError, available as dpapi_available
from .handle import ProfileRef, SecretExposure, SecretHandle
from .policy import (ActionContext, Minter, PassphraseRequired, VaultKindRefused,
                     VaultRefused, same_origin)
from .store import DEFAULT_POLICY, MODE_A, MODE_B, StoreError, default_path
from .vault import ConsumerError, Vault
from .ws import VaultWs

__all__ = [
    "ActionContext", "ConsumerError", "DEFAULT_POLICY", "DpapiError", "MODE_A", "MODE_B",
    "Minter", "PassphraseRequired", "ProfileRef", "SecretExposure", "SecretHandle",
    "StoreError", "Vault", "VaultKindRefused", "VaultRefused", "VaultWs", "default_path",
    "dpapi_available", "same_origin",
]

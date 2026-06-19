"""
chap_coordinator.profiles.identity_oidc

The identity-oidc/1.0 profile (profiles/identity-oidc.md).

OIDC binding pins the participant's signing key via the ID token's
``cnf.jwk`` claim (RFC 7800). The actual token validation is a
deployment concern; the Coordinator calls
``CoordinatorOptions.verify_oidc_token(token)`` and expects back the
claims dictionary (or None on failure).

The pinning happens inside ``Coordinator._op_participant_join`` when
an ``oidc_token`` parameter is supplied. This module exists to
document the contract and to register the step-up auth error path.

Error codes:
  -32402 step-up authentication required
  -32403 ID token invalid (signature, expiry, audience)
  -32404 cnf.jwk does not match the signing key in use
  -32405 required OIDC scope not present
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..coordinator import Coordinator


def register_identity_oidc(coord: "Coordinator") -> None:
    """No new method handlers; binding happens at participant.join when
    ``verify_oidc_token`` is configured. Step-up enforcement is wired in
    Coordinator.dispatch via the PRIVILEGED_METHODS set when
    ``enforce_step_up`` is True."""
    return None

"""
chap_coordinator.profiles.identity_vc

The identity-vc/1.0 profile (profiles/identity-vc.md).

W3C Verifiable Credentials binding: the participant presents a VP
and the Coordinator pins the proof-of-possession key. Real VP
verification is delegated to the deployment via
``CoordinatorOptions.verify_vc(presentation)`` which returns the
resolved subject (including any ``cnf_jwk``) or None.

Error codes:
  -32410 VP invalid
  -32411 holder binding invalid
  -32412 credential revoked
  -32413 credential schema unknown
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..coordinator import Coordinator


def register_identity_vc(coord: "Coordinator") -> None:
    """No new method handlers; binding happens at participant.join when
    ``verify_vc`` is configured."""
    return None

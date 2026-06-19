"""
chap_coordinator.profiles

CHAP profile implementations. Each module exports a
``register_<profile>(coord)`` function that wires its method
handlers onto a Coordinator instance.
"""

from .audit_scitt import register_audit_scitt
from .control import register_control
from .deliberation import register_deliberation
from .handoff import register_handoff
from .identity_oidc import register_identity_oidc
from .identity_vc import register_identity_vc
from .routing import register_routing
from .security_signed import register_security_signed
from .whisper import register_whisper

__all__ = [
    "register_audit_scitt",
    "register_control",
    "register_deliberation",
    "register_handoff",
    "register_identity_oidc",
    "register_identity_vc",
    "register_routing",
    "register_security_signed",
    "register_whisper",
]

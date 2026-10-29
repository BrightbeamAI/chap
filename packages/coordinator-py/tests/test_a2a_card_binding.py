"""
The Agent Card names its binding as A2A defines it.

The card advertised its interface with ``protocol_binding="jsonrpc"``, and
a2a-sdk's ``ClientFactory`` matches the A2A value ``JSONRPC`` exactly, so every
SDK client given the card failed with "no compatible transports found".
"""
from __future__ import annotations

from a2a.client import ClientConfig, ClientFactory

from chap_coordinator.transports.a2a_server import make_chap_agent_card


def test_an_sdk_client_finds_a_transport_on_the_card():
    card = make_chap_agent_card(base_url="http://127.0.0.1:9000")
    assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
    ClientFactory(ClientConfig()).create(card)

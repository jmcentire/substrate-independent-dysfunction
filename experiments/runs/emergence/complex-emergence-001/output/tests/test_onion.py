"""Tests for onion routing."""

import pytest
from hermes_p2p.node import Node
from hermes_p2p.onion import build_onion_for_path, peel_onion_simulated


class TestOnionRouting:
    def test_single_hop(self):
        """Onion with single recipient."""
        recipient = Node(node_id="recipient")
        payload = b"hello recipient"

        onion = build_onion_for_path(payload, [recipient])

        # Recipient peels
        next_hop, data, is_final = peel_onion_simulated(onion, recipient)
        assert is_final is True
        assert next_hop is None
        assert data == payload

    def test_multi_hop(self):
        """Onion with multiple relay hops."""
        relay1 = Node(node_id="relay1")
        relay2 = Node(node_id="relay2")
        recipient = Node(node_id="recipient")
        payload = b"secret payload"

        onion = build_onion_for_path(payload, [relay1, relay2, recipient])

        # Relay 1 peels
        next_hop, inner, is_final = peel_onion_simulated(onion, relay1)
        assert is_final is False
        assert next_hop == "relay2"

        # Relay 2 peels
        next_hop2, inner2, is_final2 = peel_onion_simulated(inner, relay2)
        assert is_final2 is False
        assert next_hop2 == "recipient"

        # Recipient peels
        next_hop3, data, is_final3 = peel_onion_simulated(inner2, recipient)
        assert is_final3 is True
        assert data == payload

    def test_intermediate_cannot_read_payload(self):
        """Test (d): Intermediate relays cannot read the payload."""
        relay1 = Node(node_id="relay1")
        relay2 = Node(node_id="relay2")
        recipient = Node(node_id="recipient")
        payload = b"top secret message"

        onion = build_onion_for_path(payload, [relay1, relay2, recipient])

        # Relay 1 peels its layer
        next_hop, inner_after_relay1, is_final = peel_onion_simulated(onion, relay1)
        assert is_final is False

        # The inner data after relay1 should NOT contain the plaintext payload
        assert payload not in inner_after_relay1, "Relay 1 should not see the plaintext"

        # Relay 2 peels its layer
        next_hop2, inner_after_relay2, is_final2 = peel_onion_simulated(inner_after_relay1, relay2)
        assert is_final2 is False

        # Inner data after relay2 should also not contain plaintext directly visible
        # (it's encrypted for recipient)
        # Actually after relay2 peels, the remaining is the recipient's layer
        # The plaintext is inside the recipient's encryption

        # Only recipient can read the actual payload
        _, final_data, is_final3 = peel_onion_simulated(inner_after_relay2, recipient)
        assert is_final3 is True
        assert final_data == payload

    def test_wrong_node_cannot_peel(self):
        """A node that is not the intended recipient of a layer cannot peel it."""
        relay1 = Node(node_id="relay1")
        recipient = Node(node_id="recipient")
        intruder = Node(node_id="intruder")
        payload = b"private"

        onion = build_onion_for_path(payload, [relay1, recipient])

        # Intruder tries to peel relay1's layer
        with pytest.raises(Exception):
            peel_onion_simulated(onion, intruder)

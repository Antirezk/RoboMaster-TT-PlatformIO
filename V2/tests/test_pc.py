import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc"))
from main import parse

@pytest.mark.parametrize("text,want", [("Forward 30.","forward 30"),("move left 20 cm","left 20"),("backward 100","back 100"),("Take off.","takeoff"),("stop","stop")])
def test_accept(text,want):
    assert parse(text)==want

@pytest.mark.parametrize("text", ["don't forward 30","forward 30 and land","forward 19","forward 101","power on","up 30","land on mission pad one","forward 30\nland"])
def test_reject(text):
    with pytest.raises(ValueError): parse(text)


def test_wireless_transport_loopback():
    import socket
    import threading
    from main import DatagramTransport
    # Constructor targets firmware port; replace its socket with a local test peer.
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    transport = DatagramTransport.__new__(DatagramTransport)
    transport.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    transport.sock.connect(peer.getsockname())
    transport.sock.settimeout(.1)
    try:
        assert transport.read(256) == b""
        transport.write(b"123 forward 30\n")
        data, address = peer.recvfrom(256)
        assert data == b"123 forward 30\n"
        peer.sendto(b"RESULT 123 REJECTED path-clearance\n", address)
        assert transport.read(256) == b"RESULT 123 REJECTED path-clearance\n"
    finally:
        transport.close()
        peer.close()

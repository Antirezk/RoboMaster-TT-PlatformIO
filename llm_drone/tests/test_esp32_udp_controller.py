from control.esp32_udp_controller import Esp32UdpController


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, payload, address):
        self.sent.append((payload, address))


def test_udp_transport_sends_one_ascii_datagram():
    controller = Esp32UdpController(host="192.168.4.1", port=8889)
    controller._socket = FakeSocket()
    controller.state.connected = True
    controller._write_line("rc 1 2 3 4")
    assert controller._socket.sent == [(b"rc 1 2 3 4", ("192.168.4.1", 8889))]


def test_udp_transport_reuses_protocol_parser():
    controller = Esp32UdpController()
    controller._process_line(
        "HL TEL ms=1 mission=READY airborne=0 fresh=1 safety=NORMAL override=0 "
        "f=NORMAL:900 b=NORMAL:900 l=NORMAL:900 r=NORMAL:900 "
        "mid=2 x=3 y=4 z=70 bat=75 h=65 age=10"
    )
    assert controller.telemetry.pad_id == 2
    assert controller.state.safety_status == "NORMAL"

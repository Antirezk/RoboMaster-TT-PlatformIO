"""USB high-level client. No direct TT UDP command path."""
import argparse
import logging
import re
import threading
import time
import socket

from pc_demo.config import Config
from pc_demo.voice_input import create_voice_input, normalize_phrase


def parse(text):
    text = normalize_phrase(text)
    aliases = {"take off": "takeoff", "takeoff": "takeoff", "land": "land",
               "land now": "land", "stop": "stop", "hover": "stop",
               "cancel": "stop", "battery": "battery?", "status": "status"}
    if text in aliases:
        return aliases[text]
    match = re.fullmatch(r"(?:move )?(forward|back|backward|left|right) ([1-9][0-9]{1,2})(?: (?:cm|centimeters|centimetres))?", text)
    if match and 20 <= int(match[2]) <= 100:
        return f"{'back' if match[1] == 'backward' else match[1]} {int(match[2])}"
    raise ValueError("只支持起飞、原地降落、停止、四向 20–100 cm 移动、电量和 status")


class DatagramTransport:
    def __init__(self, host):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect((host, 9000))
        self.sock.settimeout(0.1)

    def write(self, data):
        return self.sock.send(data)

    def read(self, _size):
        try:
            return self.sock.recv(4096)
        except socket.timeout:
            return b""

    def close(self):
        self.sock.close()


class Link:
    def __init__(self, port=None, host=None):
        if host:
            self.serial = DatagramTransport(host)
        else:
            import serial
            self.serial = serial.Serial(port, 115200, timeout=0.1, write_timeout=1)
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.error = None
        self.latest = "尚无 ToF 数据"
        self.counter = int(time.time())
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def write(self, text):
        if self.error:
            raise RuntimeError(self.error)
        with self.lock:
            self.serial.write((text + "\n").encode("ascii"))

    def send(self, command):
        self.counter += 1
        logging.info("[上位机请求 #%s] %s（等待底层判定）", self.counter, command)
        self.write(f"{self.counter} {command}")

    def run(self):
        beat = 0
        buffer = bytearray()
        try:
            while not self.closed.is_set():
                if time.monotonic() - beat > 0.25:
                    self.write("PING")
                    beat = time.monotonic()
                buffer.extend(self.serial.read(256))
                if len(buffer) > 8192:
                    raise RuntimeError("serial frame overflow")
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    text = line.decode("utf-8", errors="replace").strip()
                    if text.startswith("TOF "):
                        self.latest = text
                    elif text:
                        logging.info("[底层] %s", text)
        except Exception as exc:
            self.error = str(exc)
            logging.error("[连接故障] %s", exc)

    def close(self):
        self.closed.set()
        self.worker.join(timeout=2)
        self.serial.close()


def main():
    parser = argparse.ArgumentParser()
    connection = parser.add_mutually_exclusive_group(required=True)
    connection.add_argument("--port", help="ESP32 USB port, e.g. COM8")
    connection.add_argument("--host", help="ESP32 AP IP, normally 192.168.4.1 (UDP 9000)")
    parser.add_argument("--text", action="store_true", help="optional keyboard debugging")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    voice = None if args.text else create_voice_input(Config())
    link = Link(args.port, args.host)
    try:
        # USB open can reset ESP32; do not repeat any action if acknowledgement is lost.
        time.sleep(3)
        link.send("command")
        while not link.error:
            text = input("TOF > ") if args.text else voice.read()
            if not text:
                continue
            if normalize_phrase(text) in {"exit", "quit"}:
                break
            try:
                command = parse(text)
                if command == "status":
                    logging.info("[四向遥测] %s", link.latest)
                link.send(command)
            except ValueError as exc:
                logging.info("[未发送] %s", exc)
    finally:
        # Exiting only requests hover, never motoroff/emergency in flight.
        try:
            link.send("stop")
            time.sleep(0.2)
        finally:
            link.close()


if __name__ == "__main__":
    main()

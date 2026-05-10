import threading
import time
import socket

import serial
import serial.tools.list_ports

# ----------------------------
# Config
# ----------------------------
DEFAULT_PORT = "COM5"
DEFAULT_BAUD = 115200
READ_TIMEOUT_S = 0.2

# VOFA
VOFA_IP = "127.0.0.1"
VOFA_TX_PORT = 9000
VOFA_RX_PORT = 1346

tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def list_serial_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def parse_telemetry_line(line: str):
    """
    Parse STM32 telemetry line.

    Expected 12-field format:
        desired1, actual1, duty1, u1, dir1,
        desired2, actual2, duty2, u2, dir2,
        solenoid, homing

    Example:
        0.000, -0.000, 0.000, 0.000, 0, 0.000, -1.543, 0.000, 0.309, 1, 0, 0
    """
    if not line:
        return None

    line = line.strip()
    if not line:
        return None

    # Ignore ACK / ERR text lines
    if line.startswith("ACK") or line.startswith("ERR"):
        return None

    try:
        parts = [x.strip() for x in line.split(",")]

        # Must be exactly 12 fields.
        if len(parts) != 12:
            return None

        return {
            "desired1": float(parts[0]),
            "actual1": float(parts[1]),
            "duty1": float(parts[2]),
            "u1": float(parts[3]),
            "dir1": int(float(parts[4])),

            "desired2": float(parts[5]),
            "actual2": float(parts[6]),
            "duty2": float(parts[7]),
            "u2": float(parts[8]),
            "dir2": int(float(parts[9])),

            "solenoid": int(float(parts[10])),
            "homing": int(float(parts[11])),
        }

    except (ValueError, TypeError, IndexError):
        return None


class SerialReader(threading.Thread):
    """
    IMPORTANT:
    This version is adapted to your current dual-hand ShowApp.

    Queue format is always:
        ("status", text)
        ("line", text)
        ("error", text)

    Your ShowApp already separates left/right by using:
        self.serial_q_left
        self.serial_q_right

    So UART does NOT need to add hand/name into queue payload.
    """
    def __init__(self, port, baud, out_q, stop_evt):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.out_q = out_q
        self.stop_evt = stop_evt
        self.ser = None
        self._write_lock = threading.Lock()

    def _emit(self, kind: str, text: str):
        self.out_q.put((kind, text))

    def connect(self):
        self.ser = serial.Serial(
            self.port,
            self.baud,
            timeout=READ_TIMEOUT_S,
            write_timeout=READ_TIMEOUT_S
        )

        # Clear startup garbage if any
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:
            pass

        time.sleep(0.2)

    def is_connected(self):
        return self.ser is not None and self.ser.is_open

    def close(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass

    def run(self):
        try:
            self.connect()
            self._emit("status", f"Connected to {self.port} @ {self.baud}\n")
        except Exception as e:
            self._emit("error", f"Failed to open {self.port}: {e}\n")
            return

        buf = b""

        while not self.stop_evt.is_set():
            try:
                if self.ser is None or not self.ser.is_open:
                    break

                chunk = self.ser.read(256)
                if not chunk:
                    continue

                buf += chunk

                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip(b"\r")

                    try:
                        text = line.decode("utf-8", errors="replace")
                    except Exception:
                        text = str(line)

                    # Forward raw UART to VOFA TX
                    try:
                        tx_sock.sendto((text + "\n").encode("utf-8"), (VOFA_IP, VOFA_TX_PORT))
                    except Exception:
                        pass

                    self._emit("line", text)

            except Exception as e:
                self._emit("error", f"Serial read error: {e}\n")
                break

        self.close()
        self._emit("status", "Disconnected.\n")

    def write(self, s: str):
        with self._write_lock:
            if self.ser and self.ser.is_open:
                self.ser.write(s.encode("utf-8"))


class VOFAListener(threading.Thread):
    """
    Backward-compatible with your current ShowApp:
        self.vofa_listener.reader_ref = self.reader_right

    Also supports optional dual-reader routing:
        self.vofa_listener.reader_left_ref = self.reader_left
        self.vofa_listener.reader_right_ref = self.reader_right

    Command examples:
        SP=500!          -> send to reader_ref first, otherwise right, otherwise left
        LEFT:SP=500!     -> send to left board
        RIGHT:SL=35!     -> send to right board
        BOTH:RE=1!       -> send to both boards

    Emits only:
        ("status", text)
        ("error", text)

    so your current _poll_app_queue can display it directly.
    """
    def __init__(self, out_q, stop_evt):
        super().__init__(daemon=True)
        self.out_q = out_q
        self.stop_evt = stop_evt

        # backward-compatible
        self.reader_ref = None

        # optional dual routing
        self.reader_left_ref = None
        self.reader_right_ref = None

        self._sock = None

    def _emit(self, kind: str, text: str):
        self.out_q.put((kind, text))

    def _normalize_cmd(self, text: str) -> str:
        return text if (text.endswith("!") or text.endswith("\n")) else text + "\n"

    def _send_reader(self, reader, cmd: str) -> bool:
        if reader and reader.is_alive():
            try:
                reader.write(cmd)
                return True
            except Exception:
                return False
        return False

    def _dispatch(self, raw_text: str):
        text = raw_text.strip()
        if not text:
            return

        target = None
        cmd_text = text

        if ":" in text:
            maybe_target, rest = text.split(":", 1)
            tag = maybe_target.strip().upper()
            if tag in ("L", "LEFT"):
                target = "left"
                cmd_text = rest.strip()
            elif tag in ("R", "RIGHT"):
                target = "right"
                cmd_text = rest.strip()
            elif tag in ("B", "BOTH"):
                target = "both"
                cmd_text = rest.strip()

        cmd = self._normalize_cmd(cmd_text)

        if target == "left":
            ok = self._send_reader(self.reader_left_ref, cmd)
            if not ok:
                self._emit("error", f"[VOFA] left not available: {cmd_text}\n")
            return

        if target == "right":
            ok = self._send_reader(self.reader_right_ref, cmd)
            if not ok:
                self._emit("error", f"[VOFA] right not available: {cmd_text}\n")
            return

        if target == "both":
            ok_l = self._send_reader(self.reader_left_ref, cmd)
            ok_r = self._send_reader(self.reader_right_ref, cmd)
            if not (ok_l or ok_r):
                self._emit("error", f"[VOFA] both not available: {cmd_text}\n")
            return

        # default route:
        # 1) legacy reader_ref
        # 2) right
        # 3) left
        if self._send_reader(self.reader_ref, cmd):
            return
        if self._send_reader(self.reader_right_ref, cmd):
            return
        if self._send_reader(self.reader_left_ref, cmd):
            return

        self._emit("error", f"[VOFA] no serial target available: {cmd_text}\n")

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", VOFA_RX_PORT))
            self._sock.settimeout(0.5)
            self._emit("status", f"VOFA listener started on UDP:{VOFA_RX_PORT}\n")
        except Exception as e:
            self._emit("error", f"VOFA listener failed to bind UDP:{VOFA_RX_PORT}: {e}\n")
            return

        while not self.stop_evt.is_set():
            try:
                data, _addr = self._sock.recvfrom(1024)
            except socket.timeout:
                continue
            except Exception as e:
                if not self.stop_evt.is_set():
                    self._emit("error", f"VOFA listener recv error: {e}\n")
                break

            try:
                text = data.decode("utf-8", errors="replace").strip()
            except Exception:
                text = str(data)

            if not text:
                continue

            try:
                self._dispatch(text)
            except Exception as e:
                self._emit("error", f"Failed to forward VOFA cmd to MCU: {e}\n")

        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def stop(self):
        self.stop_evt.set()
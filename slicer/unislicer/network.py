"""Sending prints to printers over the network (Ethernet / Wi-Fi).

Supported connections (standard library only, no extra packages):

- ``sdcp``      ELEGOO SDCP V3 (Saturn 3/4 Ultra, Mars 5 Ultra and other ChiTu-board printers with network
                file transfer): UDP discovery ("M99999" to port 3000), HTTP upload in 1 MB packets to
                ``http://<ip>:3030/uploadFile/upload``, control and status over ``ws://<ip>:3030/websocket``.
- ``prusalink`` Prusa SL1/SL1S, MK4/XL/Core One: ``PUT /api/v1/files/<storage>/<name>``, API key or digest login.
- ``octoprint`` OctoPrint: ``POST /api/files/local`` with select/print.
- ``moonraker`` Klipper / Moonraker (Mainsail, Fluidd, Creality K1, Neptune 4...): ``POST /server/files/upload``.
- ``folder``    a shared network folder, a mapped drive or a USB stick: the file is copied there.

Printers whose makers only allow their own app or cloud (Formlabs, Asiga, Stratasys, Anycubic cloud...)
are not reachable this way; for them use ``folder`` or export the file."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import profiles

FILE_NAME = "network_printers.json"
KINDS = ("sdcp", "creality", "prusalink", "octoprint", "moonraker", "folder")
DEFAULT_PORT = {"sdcp": 3030, "creality": 80, "prusalink": 80, "octoprint": 80, "moonraker": 7125, "folder": 0}
SDCP_UDP_PORT = 3000
SDCP_CHUNK = 1024 * 1024

Progress = Callable[[float], None]


class NetworkError(Exception):
    """A failure the user can act on; the message says what happened."""


@dataclass
class NetPrinter:
    name: str
    kind: str                       # one of KINDS
    host: str = ""                  # IP or host name (folder: the folder path)
    port: int = 0                   # 0 = the default for the kind
    api_key: str = ""
    user: str = ""
    password: str = ""
    storage: str = "local"          # PrusaLink storage: local (SL1/SL1S) or usb (MK4, XL...)
    printer_id: str = ""            # the UniSlicer printer profile this machine prints with
    mainboard_id: str = ""          # SDCP
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def base_url(self) -> str:
        host = self.host.strip()
        if host.startswith(("http://", "https://")):
            return host.rstrip("/")
        port = self.port or DEFAULT_PORT.get(self.kind, 80)
        return f"http://{host}" + ("" if port == 80 else f":{port}")


# ---------------------------------------------------------------- storage
def _path(folder: str | None = None) -> str:
    return os.path.join(folder or profiles.USER_DIR, FILE_NAME)


def load_printers(folder: str | None = None) -> list[NetPrinter]:
    try:
        with open(_path(folder), "r", encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            out.append(NetPrinter(**{k: v for k, v in r.items() if k in NetPrinter.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def save_printers(items: list[NetPrinter], folder: str | None = None) -> None:
    path = _path(folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump([asdict(p) for p in items], fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- HTTP helpers
class _Body:
    """File-like request body (prefix + file + suffix) that reports upload progress."""

    def __init__(self, parts: list, progress: Progress | None, cancelled=None):
        self.parts = parts                         # bytes, or (path, offset, length)
        self.total = sum(len(p) if isinstance(p, bytes) else p[2] for p in parts)
        self.sent = 0
        self.progress = progress
        self.cancelled = cancelled
        self._i = 0
        self._fh = None
        self._left = 0
        self._buf = b""

    def __len__(self):
        return self.total

    def read(self, n: int = 65536) -> bytes:
        if self.cancelled and self.cancelled():
            raise InterruptedError()
        while not self._buf and not self._left and self._i < len(self.parts):
            part = self.parts[self._i]
            self._i += 1
            if isinstance(part, bytes):
                self._buf = part
            else:
                self._fh = open(part[0], "rb")
                self._fh.seek(part[1])
                self._left = part[2]
        if self._buf:
            out, self._buf = self._buf[:n], self._buf[n:]
        elif self._left:
            out = self._fh.read(min(n, self._left))
            self._left -= len(out)
            if not self._left:
                self._fh.close()
        else:
            out = b""
        self.sent += len(out)
        if self.progress and self.total:
            self.progress(self.sent / self.total)
        return out


def _multipart(fields: dict, file_field: str, filename: str, file_part, progress=None, cancelled=None):
    boundary = "----UniSlicer" + uuid.uuid4().hex
    pre = b""
    for k, v in fields.items():
        pre += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    pre += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    body = _Body([pre, file_part, post], progress, cancelled)
    return body, f"multipart/form-data; boundary={boundary}"


def _open(p: NetPrinter, method: str, path: str, body=None, headers=None, timeout: float = 15.0):
    url = p.base_url() + path
    h = dict(headers or {})
    if p.api_key:
        h["X-Api-Key"] = p.api_key
    if body is not None and hasattr(body, "read"):
        h["Content-Length"] = str(len(body))
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    handlers = []
    if p.user and not p.api_key:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, p.base_url(), p.user, p.password)
        handlers += [urllib.request.HTTPDigestAuthHandler(mgr), urllib.request.HTTPBasicAuthHandler(mgr)]
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        if e.code in (401, 403):
            raise NetworkError("auth") from e
        raise NetworkError(f"HTTP {e.code}: {raw[:200].decode('utf-8', 'replace')}") from e
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise NetworkError(f"unreachable: {reason}") from e


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        return None


# ---------------------------------------------------------------- WebSocket (for SDCP)
class WebSocket:
    """Minimal RFC 6455 client: text frames, ping/pong, close."""

    def __init__(self, host: str, port: int, path: str = "/websocket", timeout: float = 8.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise NetworkError("websocket closed during handshake")
            head += chunk
        head, self._rest = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0] + b" ":
            raise NetworkError("websocket refused: " + head.split(b"\r\n", 1)[0].decode("latin-1"))
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if accept not in head:
            raise NetworkError("websocket handshake mismatch")

    def _recv_exact(self, n: int) -> bytes:
        out = b""
        if self._rest:
            out, self._rest = self._rest[:n], self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise NetworkError("websocket closed")
            out += chunk
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self) -> str | None:
        """Next text message (None when the server closes)."""
        while True:
            b0, b1 = self._recv_exact(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:
                self._send_frame(0xA, data)
            elif op == 0x8:
                return None
            elif op in (0x1, 0x0):
                return data.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- SDCP (ELEGOO and ChiTu boards)
def _sdcp_parse(raw: bytes, addr: str) -> dict | None:
    j = _json(raw)
    if not isinstance(j, dict) or not isinstance(j.get("Data"), dict):
        return None
    d = j["Data"]
    a = d.get("Attributes") if isinstance(d.get("Attributes"), dict) else d   # both reply shapes exist
    return {"kind": "sdcp", "name": a.get("Name") or a.get("MachineName") or addr,
            "model": a.get("MachineName", ""), "host": a.get("MainboardIP") or addr,
            "mainboard_id": a.get("MainboardID", ""), "firmware": a.get("FirmwareVersion", ""),
            "resolution": a.get("Resolution", ""), "brand": a.get("BrandName", "")}


def discover(timeout: float = 2.0, targets: list[str] | None = None) -> list[dict]:
    """Find SDCP printers: broadcast "M99999" to UDP 3000 and collect the replies."""
    found: dict[str, dict] = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        s.bind(("", 0))
        for t in targets or ["255.255.255.255"]:
            try:
                s.sendto(b"M99999", (t, SDCP_UDP_PORT))
            except OSError:
                pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw, (addr, _port) = s.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            info = _sdcp_parse(raw, addr)
            if info:
                found[info["host"]] = info
    finally:
        s.close()
    return list(found.values())


# ---------------------------------------------------------------- whole-network scan
SCAN_PORTS = (80, 7125, 4408, 4409, 5000, 9999, 3030)


def local_networks() -> list[str]:
    """IPv4 prefixes (a.b.c) of this computer's local networks (Wi-Fi and cable)."""
    found = []
    try:                                         # the address used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    out = []
    for ip in found:
        if ip.startswith(("127.", "169.254.")) or ip.count(".") != 3:
            continue
        prefix = ip.rsplit(".", 1)[0]
        if prefix not in out:
            out.append(prefix)
    return out


def _open_port(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_json(host: str, port: int, path: str, timeout: float = 1.5):
    """(status, json or None, headers) of a GET, without raising."""
    url = f"http://{host}" + ("" if port == 80 else f":{port}") + path
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                    timeout=timeout) as r:
            return r.status, _json(r.read(65536)), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, None, dict(e.headers or {})
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None, {}


def identify(host: str, ports: set[int]) -> list[dict]:
    """What printer service answers on ``host`` (given its open ports)."""
    out = []
    for port in [p for p in (7125, 4408, 4409, 80) if p in ports]:
        code, j, _ = _http_json(host, port, "/server/info")
        if code == 200 and isinstance(j, dict) and isinstance(j.get("result"), dict):
            _, pi, _ = _http_json(host, port, "/printer/info")
            info = (pi or {}).get("result") or {} if isinstance(pi, dict) else {}
            out.append({"kind": "moonraker", "host": host, "port": port,
                         "name": info.get("hostname") or host, "model": "Klipper / Moonraker"})
            if 9999 in ports:                      # a Creality running Moonraker: say which one
                c = creality_info(host)
                if c is not None:
                    out[-1]["model"] = c["model"] + " (Moonraker)"
                    out[-1]["name"] = c["name"]
            break
    if 80 in ports or 5000 in ports:
        for port in [p for p in (80, 5000) if p in ports]:
            code, j, headers = _http_json(host, port, "/api/version")
            text = json.dumps(j) if j is not None else ""
            auth = (headers.get("WWW-Authenticate") or headers.get("www-authenticate") or "").lower()
            if "prusalink" in text.lower() or "prusa" in text.lower() or (code == 401 and "printer api" in auth):
                out.append({"kind": "prusalink", "host": host, "port": port, "name": (j or {}).get("hostname") or host,
                            "model": (j or {}).get("text") or "Prusa (PrusaLink)"})
                break
            if "octoprint" in text.lower() or (code in (401, 403) and port == 5000):
                out.append({"kind": "octoprint", "host": host, "port": port, "name": host,
                            "model": (j or {}).get("text") or "OctoPrint"})
                break
    if 9999 in ports and not any(o["kind"] == "moonraker" for o in out):
        info = creality_info(host)
        if info is not None:
            out.append(info)
    return out


def scan(progress=None, cancelled=None, timeout: float = 0.3, prefixes: list[str] | None = None) -> list[dict]:
    """Find printers on the local network: SDCP broadcast, then every address of the local network(s)
    probed on the ports printers listen on (Moonraker / Klipper incl. Creality, PrusaLink, OctoPrint,
    Creality LAN mode, ELEGOO SDCP)."""
    from concurrent.futures import ThreadPoolExecutor
    found: dict[tuple, dict] = {}
    for h in discover(1.5):
        found[(h["host"], "sdcp")] = h
    prefixes = prefixes if prefixes is not None else local_networks()
    hosts = [f"{p}.{i}" for p in prefixes for i in range(1, 255)]
    jobs = [(h, port) for h in hosts for port in SCAN_PORTS]
    open_ports: dict[str, set] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=128) as pool:
        for (h, port), ok in zip(jobs, pool.map(lambda hp: _open_port(hp[0], hp[1], timeout), jobs)):
            done += 1
            if progress and done % 64 == 0:
                progress(0.8 * done / len(jobs))
            if cancelled and cancelled():
                raise InterruptedError()
            if ok:
                open_ports.setdefault(h, set()).add(port)
        candidates = [h for h, ports in open_ports.items() if ports & {80, 7125, 4408, 4409, 5000, 9999, 3030}]
        for h, results in zip(candidates, pool.map(lambda h: identify(h, open_ports[h]), candidates)):
            for r in results:
                found.setdefault((r["host"], r["kind"]), r)
            if 3030 in open_ports[h] and (h, "sdcp") not in found:
                found[(h, "sdcp")] = {"kind": "sdcp", "host": h, "name": h, "model": "SDCP (ELEGOO / ChiTu)",
                                      "mainboard_id": ""}
    if progress:
        progress(1.0)
    return sorted(found.values(), key=lambda d: tuple(int(x) for x in d["host"].split(".")) if d["host"].count(".") == 3 else (999,))


def _creality_ws(host: str, timeout: float = 3.0) -> "WebSocket":
    """The Creality LAN-mode WebSocket (port 9999; path "/" on most firmware, "/websocket" on some)."""
    last = None
    for path in ("/", "/websocket"):
        try:
            return WebSocket(host, 9999, path, timeout=timeout)
        except (NetworkError, OSError) as e:
            last = e
    raise NetworkError(f"unreachable: Creality LAN mode (port 9999) did not answer ({last})")


def _creality_read(ws: "WebSocket", until, seconds: float = 3.0) -> dict:
    """Collect the printer's JSON telemetry until ``until(telemetry)`` or the time is up; answers heartbeats."""
    telem: dict = {}
    ws.sock.settimeout(seconds)
    end = time.time() + seconds
    try:
        while time.time() < end:
            msg = ws.recv()
            if msg is None:
                break
            if msg.strip() == "ok":
                continue
            j = _json(msg.encode())
            if not isinstance(j, dict):
                continue
            if j.get("ModeCode") == "heart_beat":
                ws.send("ok")
                continue
            telem.update(j)
            if until(telem):
                break
    except (socket.timeout, OSError, NetworkError):
        pass
    return telem


def _norm_colour(c) -> str:
    """CFS colours come as "#0RRGGBB" / "0RRGGBB" / "#RRGGBB"."""
    h = "".join(ch for ch in str(c or "") if ch in "0123456789abcdefABCDEF")
    return "#" + h[-6:].upper() if len(h) >= 6 else ""


def parse_cfs_boxes(boxs_info) -> list[dict]:
    """The filaments in a Creality CFS (boxsInfo.materialBoxs, as K2 / SPARKX firmware reports it):
    one entry per slot with its label (1A..4D, or EXT for the external spool), colour, type and name."""
    if isinstance(boxs_info, dict) and "boxsInfo" in boxs_info and "materialBoxs" not in boxs_info:
        boxs_info = boxs_info["boxsInfo"]
    boxes = (boxs_info or {}).get("materialBoxs") or [] if isinstance(boxs_info, dict) else []
    out = []
    for box in boxes:
        if not isinstance(box, dict):
            continue
        is_cfs = box.get("type") in (0, 2) and int(box.get("id", 0) or 0) >= 1
        for k, m in enumerate(box.get("materials") or []):
            if not isinstance(m, dict):
                continue
            loaded = bool(m.get("state")) and bool(m.get("vendor") or m.get("type") or m.get("name"))
            idx = int(m.get("id", k) if str(m.get("id", k)).isdigit() else k)
            out.append({
                "slot": f"{int(box.get('id', 1))}{chr(65 + idx)}" if is_cfs else "EXT",
                "colour": _norm_colour(m.get("color")),
                "type": str(m.get("type") or "").upper(),
                "name": str(m.get("name") or ""),
                "vendor": str(m.get("vendor") or ""),
                "loaded": loaded,
                "percent": m.get("percent") if isinstance(m.get("percent"), (int, float)) else None,
            })
    return out


def creality_cfs_slots(host: str, timeout: float = 4.0) -> list[dict]:
    """Ask a Creality printer (LAN mode, port 9999) which filaments are in its CFS slots right now."""
    ws = _creality_ws(host, timeout)
    try:
        ws.send(json.dumps({"method": "get", "params": {"boxsInfo": 1}}))
        telem = _creality_read(ws, lambda t: "boxsInfo" in t, timeout)
    finally:
        ws.close()
    if "boxsInfo" not in telem:
        raise NetworkError("no answer from the printer: no CFS information (is the CFS connected?)")
    return parse_cfs_boxes(telem["boxsInfo"])


def creality_info(host: str) -> dict | None:
    """Creality LAN mode (K1 / K2 / Hi / SPARKX i7 stock firmware): a WebSocket on port 9999 that sends
    the printer's state as JSON. Returns what it says about itself, or None."""
    try:
        ws = _creality_ws(host, 2.0)
    except NetworkError:
        return None
    try:
        ws.send(json.dumps({"method": "get", "params": {"ReqPrinterPara": 1}}))
        info = _creality_read(ws, lambda t: any(k in t for k in ("model", "modelVersion", "hostname")), 2.5)
    finally:
        ws.close()
    model = str(info.get("model") or info.get("modelVersion") or "")
    return {"kind": "creality", "host": host, "port": 80, "name": str(info.get("hostname") or host),
            "model": ("Creality " + model).strip() if not model.lower().startswith("creality") else model}


class _Creality:
    """Creality stock firmware in LAN mode (turn on 'LAN mode' / 'LAN only' in the printer's network
    settings): files are uploaded over HTTP (POST /upload/<name>, field "file") and printed through the
    port-9999 WebSocket. When the printer also runs Moonraker (port 7125), the Moonraker connection is
    used instead because it is documented."""
    GCODE_DIRS = ("/mnt/UDISK/printer_data/gcodes", "/usr/data/printer_data/gcodes")

    def __init__(self, p):
        self.p = p

    def _moonraker(self):
        if _open_port(self.p.host, 7125, 0.8):
            code, j, _ = _http_json(self.p.host, 7125, "/server/info")
            if code == 200 and isinstance(j, dict) and "result" in j:
                return _Moonraker(NetPrinter(self.p.name, "moonraker", self.p.host, 7125, self.p.api_key))
        return None

    def test(self):
        m = self._moonraker()
        info = creality_info(self.p.host)
        if info is None and m is None:
            raise NetworkError("unreachable: no answer on port 9999 - turn on LAN mode in the printer's "
                               "network settings")
        text = info["model"] if info else "Creality"
        return text + (" · Moonraker" if m else " · LAN mode")

    def upload(self, path, name, progress=None, cancelled=None, start=False):
        m = self._moonraker()
        if m is not None:
            return m.upload(path, name, progress, cancelled, start)
        body, ctype = _multipart({}, "file", name, (path, 0, os.path.getsize(path)), progress, cancelled)
        port = self.p.port or 80
        q = NetPrinter(self.p.name, "creality", self.p.host, port, self.p.api_key)
        _open(q, "POST", f"/upload/{urllib.parse.quote(name)}", body, {"Content-Type": ctype}, timeout=180)
        if start:
            self.start(name)

    def start(self, name):
        ws = _creality_ws(self.p.host)
        try:
            ws.send(json.dumps({"method": "get", "params": {"ReqPrinterPara": 1}}))
            telem = _creality_read(ws, lambda t: "model" in t or "hostname" in t, 2.0)
            model = str(telem.get("model", "")).upper()
            folder = self.GCODE_DIRS[1] if ("K1" in model or "HI" in model or "ENDER" in model) else self.GCODE_DIRS[0]
            ws.send(json.dumps({"method": "set", "params": {"opGcodeFile": f"printprt:{folder}/{name}",
                                                             "enableSelfTest": 0}}))
            _creality_read(ws, lambda t: t.get("state") in (1, "1") or "printFileName" in t, 3.0)
        finally:
            ws.close()

    def status(self):
        m = self._moonraker()
        if m is not None:
            return m.status()
        ws = _creality_ws(self.p.host)
        try:
            ws.send(json.dumps({"method": "get", "params": {"ReqPrinterPara": 1}}))
            t = _creality_read(ws, lambda t: "state" in t and "printProgress" in t, 3.0)
        finally:
            ws.close()
        states = {0: "idle", 1: "printing", 2: "paused", 3: "complete"}
        st = t.get("state", t.get("deviceState"))
        try:
            st = states.get(int(st), str(st))
        except (TypeError, ValueError):
            st = str(st or "")
        name = str(t.get("printFileName", "")).split("/")[-1]
        prog = t.get("printProgress", t.get("dProgress"))
        return {"state": st, "file": name, "layer": t.get("layer"), "layers": t.get("TotalLayer"),
                "percent": float(prog) if isinstance(prog, (int, float)) else None}

    def control(self, action):
        m = self._moonraker()
        if m is not None:
            return m.control(action)
        ws = _creality_ws(self.p.host)
        try:
            ws.send(json.dumps({"method": "set", "params": {"pause": {"pause": 1, "resume": 0}[action]}
                                if action in ("pause", "resume") else {"stop": 1}}))
            _creality_read(ws, lambda t: "state" in t, 2.0)
        finally:
            ws.close()


class _Sdcp:
    def __init__(self, p: NetPrinter):
        self.p = p

    def _ids(self) -> str:
        if not self.p.mainboard_id:
            hits = discover(1.5, [self.p.host])
            if not hits:
                raise NetworkError("unreachable: no SDCP reply from " + self.p.host)
            self.p.mainboard_id = hits[0]["mainboard_id"]
        return self.p.mainboard_id

    def _request(self, cmd: int, data: dict, want: str = "response", timeout: float = 8.0) -> dict:
        mid = self._ids()
        ws = WebSocket(self.p.host, self.p.port or 3030)
        try:
            rid = uuid.uuid4().hex
            ws.send(json.dumps({"Id": uuid.uuid4().hex, "Data": {"Cmd": cmd, "Data": data, "RequestID": rid,
                                "MainboardID": mid, "TimeStamp": int(time.time()), "From": 0},
                                "Topic": f"sdcp/request/{mid}"}))
            end = time.time() + timeout
            ws.sock.settimeout(timeout)
            while time.time() < end:
                msg = ws.recv()
                if msg is None:
                    break
                j = _json(msg.encode())
                if not isinstance(j, dict):
                    continue
                topic = str(j.get("Topic", ""))
                if want == "status" and "status" in topic:
                    return j
                d = j.get("Data") or {}
                if want == "response" and "response" in topic and d.get("RequestID") in (rid, None):
                    return d.get("Data") or {}
            raise NetworkError("no answer from the printer")
        except socket.timeout as e:
            raise NetworkError("no answer from the printer") from e
        finally:
            ws.close()

    def test(self) -> str:
        hits = discover(1.5, [self.p.host])
        if not hits:
            raise NetworkError("unreachable: no SDCP reply from " + self.p.host)
        h = hits[0]
        self.p.mainboard_id = h["mainboard_id"]
        return f"{h['model'] or h['name']} · FW {h['firmware']}".strip(" ·")

    def upload(self, path: str, name: str, progress=None, cancelled=None) -> None:
        size = os.path.getsize(path)
        md5 = hashlib.md5()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                md5.update(block)
        file_id = uuid.uuid4().hex
        offset = 0
        while offset < size or size == 0:
            n = min(SDCP_CHUNK, size - offset)
            body, ctype = _multipart({"S-File-MD5": md5.hexdigest(), "Check": "1", "Offset": str(offset),
                                      "Uuid": file_id, "TotalSize": str(size)}, "File", name,
                                     (path, offset, n),
                                     (lambda f, o=offset, n=n: progress((o + f * n) / max(size, 1))) if progress else None,
                                     cancelled)
            _, raw = _open(self.p, "POST", "/uploadFile/upload", body, {"Content-Type": ctype}, timeout=60)
            j = _json(raw) or {}
            if not j.get("success", False):
                raise NetworkError(f"upload refused: {raw[:200].decode('utf-8', 'replace')}")
            offset += n
            if size == 0:
                break

    def start(self, name: str) -> None:
        ack = self._request(128, {"Filename": name, "StartLayer": 0}).get("Ack", 0)
        if ack:
            reasons = {1: "busy", 2: "file not found", 3: "MD5 check failed", 4: "file read error",
                       5: "wrong resolution for this printer", 6: "unknown file format", 7: "file is for another model"}
            raise NetworkError("printer refused: " + reasons.get(ack, f"code {ack}"))

    def status(self) -> dict:
        j = self._request(0, {}, want="status")
        st = (j.get("Data") or {}).get("Status") or j.get("Status") or {}
        info = st.get("PrintInfo") or {}
        states = {0: "idle", 1: "printing", 2: "receiving file", 3: "exposure test", 4: "device test"}
        cur = st.get("CurrentStatus")
        cur = cur[0] if isinstance(cur, list) and cur else cur
        return {"state": states.get(cur, str(cur)), "file": info.get("Filename", ""),
                "layer": info.get("CurrentLayer"), "layers": info.get("TotalLayer")}

    def control(self, action):
        ack = self._request({"pause": 129, "stop": 130, "resume": 131}[action], {}).get("Ack", 0)
        if ack:
            raise NetworkError(f"printer refused: code {ack}")


# ---------------------------------------------------------------- HTTP printers
class _OctoPrint:
    def __init__(self, p):
        self.p = p

    def test(self):
        _, raw = _open(self.p, "GET", "/api/version")
        j = _json(raw) or {}
        return j.get("text") or f"OctoPrint {j.get('server', '')}"

    def upload(self, path, name, progress=None, cancelled=None, start=False):
        body, ctype = _multipart({"select": "true" if start else "false", "print": "true" if start else "false"},
                                 "file", name, (path, 0, os.path.getsize(path)), progress, cancelled)
        _open(self.p, "POST", "/api/files/local", body, {"Content-Type": ctype}, timeout=120)

    def status(self):
        _, raw = _open(self.p, "GET", "/api/job")
        j = _json(raw) or {}
        prog = (j.get("progress") or {}).get("completion")
        return {"state": j.get("state", ""), "file": ((j.get("job") or {}).get("file") or {}).get("name", ""),
                "percent": prog}

    def control(self, action):
        body = {"pause": {"command": "pause", "action": "pause"}, "resume": {"command": "pause", "action": "resume"},
                "stop": {"command": "cancel"}}[action]
        _open(self.p, "POST", "/api/job", json.dumps(body).encode(), {"Content-Type": "application/json"})


class _Moonraker:
    def __init__(self, p):
        self.p = p

    def test(self):
        _, raw = _open(self.p, "GET", "/server/info")
        r = (_json(raw) or {}).get("result") or {}
        return f"Klipper: {r.get('klippy_state', '?')} · Moonraker {r.get('moonraker_version', '')}".strip(" ·")

    def upload(self, path, name, progress=None, cancelled=None, start=False):
        body, ctype = _multipart({"root": "gcodes", "print": "true" if start else "false"}, "file", name,
                                 (path, 0, os.path.getsize(path)), progress, cancelled)
        _open(self.p, "POST", "/server/files/upload", body, {"Content-Type": ctype}, timeout=120)

    def status(self):
        _, raw = _open(self.p, "GET", "/printer/objects/query?print_stats&display_status")
        s = ((_json(raw) or {}).get("result") or {}).get("status") or {}
        ps, ds = s.get("print_stats") or {}, s.get("display_status") or {}
        p = ds.get("progress")
        return {"state": ps.get("state", ""), "file": ps.get("filename", ""),
                "percent": None if p is None else round(p * 100, 1)}

    def control(self, action):
        _open(self.p, "POST", "/printer/print/" + {"pause": "pause", "resume": "resume", "stop": "cancel"}[action], b"")


class _PrusaLink:
    def __init__(self, p):
        self.p = p

    def test(self):
        _, raw = _open(self.p, "GET", "/api/version")
        j = _json(raw) or {}
        return j.get("text") or f"PrusaLink {j.get('server', '')}".strip()

    def upload(self, path, name, progress=None, cancelled=None, start=False):
        storage = (self.p.storage or "local").strip("/")
        body = _Body([(path, 0, os.path.getsize(path))], progress, cancelled)
        _open(self.p, "PUT", f"/api/v1/files/{storage}/{urllib.parse.quote(name)}", body,
              {"Content-Type": "application/octet-stream", "Overwrite": "?1",
               "Print-After-Upload": "?1" if start else "?0"}, timeout=180)

    def status(self):
        _, raw = _open(self.p, "GET", "/api/v1/status")
        j = _json(raw) or {}
        job = j.get("job") or {}
        return {"state": (j.get("printer") or {}).get("state", ""), "file": "", "percent": job.get("progress")}

    def control(self, action):
        _, raw = _open(self.p, "GET", "/api/v1/status")
        job = (_json(raw) or {}).get("job") or {}
        if "id" not in job:
            raise NetworkError("not printing")
        if action == "stop":
            _open(self.p, "DELETE", f"/api/v1/job/{job['id']}")
        else:
            _open(self.p, "PUT", f"/api/v1/job/{job['id']}/{action}", b"")


class _Folder:
    def __init__(self, p):
        self.p = p

    def test(self):
        d = self.p.host
        if not d or not os.path.isdir(d):
            raise NetworkError("folder not found: " + (d or "-"))
        if not os.access(d, os.W_OK):
            raise NetworkError("auth")
        free = shutil.disk_usage(d).free / 1e9
        return f"{d} · {free:.1f} GB free"

    def upload(self, path, name, progress=None, cancelled=None, start=False):
        self.test()
        dest = os.path.join(self.p.host, name)
        size = max(1, os.path.getsize(path))
        done = 0
        with open(path, "rb") as src, open(dest + ".part", "wb") as out:
            for block in iter(lambda: src.read(1 << 20), b""):
                if cancelled and cancelled():
                    raise InterruptedError()
                out.write(block)
                done += len(block)
                if progress:
                    progress(done / size)
        os.replace(dest + ".part", dest)

    def status(self):
        return {"state": "folder", "file": "", "percent": None}

    def control(self, action):
        raise NetworkError("not supported: a folder connection cannot pause or stop a print")


def client(p: NetPrinter):
    return {"sdcp": _Sdcp, "creality": _Creality, "octoprint": _OctoPrint, "moonraker": _Moonraker,
            "prusalink": _PrusaLink, "folder": _Folder}[p.kind](p)


def test_connection(p: NetPrinter) -> str:
    return client(p).test()


def send(p: NetPrinter, path: str, name: str | None = None, start: bool = False,
         progress: Progress | None = None, cancelled=None) -> str:
    """Upload ``path`` to the printer (and start printing if asked). Returns the name used on the printer."""
    name = name or os.path.basename(path)
    c = client(p)
    if p.kind == "sdcp":
        c.upload(path, name, progress, cancelled)
        if start:
            c.start(name)
    else:
        c.upload(path, name, progress, cancelled, start=start)
    return name


def printer_status(p: NetPrinter) -> dict:
    return client(p).status()


CONTROL_ACTIONS = ("pause", "resume", "stop")


def control(p: NetPrinter, action: str) -> None:
    """Pause, resume or stop the print running on a network printer."""
    if action not in CONTROL_ACTIONS:
        raise NetworkError("unknown action: " + action)
    client(p).control(action)


def can_control(kind: str) -> bool:
    return kind != "folder"


def can_start(kind: str) -> bool:
    return kind != "folder"


def explain(err: Exception, lang: str = "he") -> str:
    """A NetworkError in plain words, with what to check."""
    msg = str(err)
    he = lang == "he"
    if msg == "auth":
        return ("הגישה נדחתה: בדוק את מפתח ה-API או שם המשתמש והסיסמה (במסך ההגדרות של המדפסת)." if he else
                "Access denied: check the API key or user name and password (in the printer's settings screen).")
    if msg.startswith("unreachable") or msg.startswith("no answer"):
        return ("המדפסת לא עונה. בדוק שהיא דולקת ומחוברת לאותה רשת (Wi-Fi/כבל) כמו המחשב, שכתובת ה-IP נכונה "
                "(מופיעה במסך הרשת של המדפסת), ושחומת האש לא חוסמת את UniSlicer." if he else
                "The printer does not answer. Check it is on and on the same network (Wi-Fi/cable) as this "
                "computer, that the IP address is right (shown on the printer's network screen), and that the "
                "firewall does not block UniSlicer.") + f"\n({msg})"
    if msg.startswith("folder not found"):
        return ("התיקייה לא נמצאה. בדוק שהכונן המשותף או ה-USB מחוברים." if he else
                "Folder not found. Check that the shared drive or USB stick is connected.") + f"\n({msg})"
    return msg

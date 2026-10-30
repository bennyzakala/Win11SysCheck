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
KINDS = ("sdcp", "prusalink", "octoprint", "moonraker", "folder")
DEFAULT_PORT = {"sdcp": 3030, "prusalink": 80, "octoprint": 80, "moonraker": 7125, "folder": 0}
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


def client(p: NetPrinter):
    return {"sdcp": _Sdcp, "octoprint": _OctoPrint, "moonraker": _Moonraker, "prusalink": _PrusaLink,
            "folder": _Folder}[p.kind](p)


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

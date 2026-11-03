"""Sending prints to Formlabs printers (Form 4/4B, Form 3+/3B+...) through Formlabs' own Local API.

Formlabs printers only take jobs prepared by PreForm, so the work goes through PreFormServer: Formlabs'
free background program (a zip from Formlabs' "API downloads" page, no account needed for printers on the
local network) or PreForm itself (File > Start PreForm Server). UniSlicer sends the models it arranged and
turned, PreForm makes the layers with the chosen Formlabs material, and the job goes to the printer.

API (0.9.x): http://localhost:44388 - POST /scene/, POST /scene/{id}/import-model/, auto-orient,
auto-support, auto-layout, GET print-validation, POST estimate-print-time, POST /discover-devices/,
GET /devices/, POST /scene/{id}/print/, POST save-form. Long calls run with ?async=true and are polled at
/operations/{id}/. Standard library only."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

DEFAULT_PORT = 44388
DOWNLOAD_PAGE = "https://formlabs.com/support/Formlabs-API-downloads-and-release-notes/"
EXE_NAMES = ("PreFormServer.exe", "PreFormServer")


class FormlabsError(RuntimeError):
    """A PreFormServer error, with Formlabs' stable error code (e.g. PRINTER_IP_NOT_FOUND)."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def candidate_paths() -> list[str]:
    """Where PreFormServer is usually unzipped (there is no installer, so these are conventions)."""
    out = []
    for env, sub in (("LOCALAPPDATA", r"Formlabs\PreFormServer"), ("LOCALAPPDATA", "PreFormServer"),
                     ("ProgramFiles", r"Formlabs\PreFormServer"), ("ProgramFiles", "PreFormServer"),
                     ("USERPROFILE", r"Downloads\PreFormServer"), ("USERPROFILE", r"Desktop\PreFormServer")):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, sub, EXE_NAMES[0]))
    for name in EXE_NAMES:
        found = shutil.which(name)
        if found:
            out.append(found)
    return out


def find_server(saved: str = "") -> str:
    """Path of PreFormServer: the one the user chose, else the first usual place that has it ("" = not found)."""
    for p in ([saved] if saved else []) + candidate_paths():
        if p and os.path.isfile(p):
            return p
    return ""


class Client:
    """A small Local API client (urllib)."""

    def __init__(self, host: str = "localhost", port: int = DEFAULT_PORT, timeout: float = 620.0):
        self.base = f"http://{host}:{int(port)}"
        self.timeout = timeout
        self.process: subprocess.Popen | None = None

    # ------------------------------------------------------------ plumbing
    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            try:
                err = (json.loads(raw) or {}).get("error") or {}
            except ValueError:
                err = {}
            raise FormlabsError(err.get("message") or f"HTTP {e.code}", err.get("code", "")) from None
        except (urllib.error.URLError, OSError) as e:
            raise FormlabsError(f"PreFormServer is not reachable at {self.base} ({e})", "NOT_RUNNING") from None
        if not raw:
            return status, {}
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {}

    def call(self, method: str, path: str, body: dict | None = None, progress: Callable[[float], None] | None = None,
             cancelled: Callable[[], bool] | None = None, poll: float = 0.5):
        """Run a long operation with ?async=true and wait for its result (a 200 answer is also accepted)."""
        sep = "&" if "?" in path else "?"
        status, reply = self.request(method, f"{path}{sep}async=true", body)
        if status != 202:
            return reply
        op = reply.get("operationId") or str(reply.get("operationUrl", "")).rstrip("/").rsplit("/", 1)[-1]
        start = time.time()
        while True:
            if cancelled and cancelled():
                raise InterruptedError()
            _, st = self.request("GET", f"/operations/{op}/?mode=oneshot")
            if progress and isinstance(st.get("progress"), (int, float)):
                progress(float(st["progress"]))
            state = st.get("status")
            if state == "SUCCEEDED":
                return st.get("result") or {}
            if state == "FAILED":
                err = (st.get("result") or {}).get("error") or {}
                raise FormlabsError(err.get("message") or "PreFormServer operation failed", err.get("code", ""))
            if time.time() - start > self.timeout:
                raise FormlabsError("PreFormServer did not finish in time", "TIMEOUT")
            time.sleep(poll)

    # ------------------------------------------------------------ server
    def version(self) -> str:
        _, r = self.request("GET", "/", timeout=3.0)
        return str(r.get("version", ""))

    def running(self) -> bool:
        try:
            self.version()
            return True
        except FormlabsError:
            return False

    def start(self, exe: str, wait: float = 120.0, log: Callable[[str], None] | None = None) -> None:
        """Start PreFormServer (if it is not already answering) and wait for "READY FOR INPUT"."""
        if self.running():
            return
        if not exe or not os.path.isfile(exe):
            raise FormlabsError("PreFormServer was not found", "NOT_INSTALLED")
        port = self.base.rsplit(":", 1)[-1]
        env = dict(os.environ, DISABLE_PREFORMSERVER_TELEMETRY="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen([exe, "--port", port], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        cwd=os.path.dirname(exe), env=env, creationflags=flags, text=True,
                                        errors="replace")
        ready = threading.Event()
        lines: list[str] = []

        def read():
            for line in self.process.stdout:            # keep reading, or a full pipe would stall the server
                lines.append(line)
                if log:
                    log(line.rstrip())
                if "READY FOR INPUT" in line:
                    ready.set()

        threading.Thread(target=read, daemon=True).start()
        end = time.time() + wait
        while time.time() < end:
            if ready.is_set() or self.running():
                return
            if self.process.poll() is not None:
                tail = "".join(lines[-5:]).strip()
                if "already in use" in tail:
                    raise FormlabsError(f"Port {port} is already in use by another program", "PORT_IN_USE")
                raise FormlabsError("PreFormServer stopped: " + (tail or "no output"), "SERVER_EXITED")
            time.sleep(0.3)
        raise FormlabsError("PreFormServer did not start in time", "TIMEOUT")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.process = None

    # ------------------------------------------------------------ catalogue and printers
    def materials(self) -> list[dict]:
        """[{"label": "Form 4", "product_names": [...], "materials": [{"label", "settings": [{"label",
        "scene_settings"}]}]}] - straight from the server's own list (codes are never guessed)."""
        _, r = self.request("GET", "/list-materials/")
        out = []
        for pt in r.get("printer_types", []) or []:
            mats = []
            for m in pt.get("materials", []) or []:
                settings = [{"label": s.get("label", ""), "scene_settings": s.get("scene_settings") or {}}
                            for s in m.get("material_settings", []) or [] if s.get("scene_settings")]
                if settings:
                    mats.append({"label": m.get("label", ""), "description": m.get("description", ""),
                                 "settings": settings})
            if mats:
                out.append({"label": pt.get("label", ""), "product_names": list(pt.get("supported_product_names") or []),
                            "machine_types": list(pt.get("supported_machine_type_ids") or []), "materials": mats})
        return out

    def discover(self, timeout_s: int = 10, ip: str = "", cancelled=None) -> list[dict]:
        """Look for Formlabs printers on the local network (or at one address), then list every printer the
        server knows that can take a print."""
        body = {"ip_address": ip} if ip else {"timeout_seconds": int(timeout_s)}
        self.call("POST", "/discover-devices/", body, cancelled=cancelled)
        return self.devices()

    def devices(self) -> list[dict]:
        _, r = self.request("GET", "/devices/?can_print=true")
        return [d for d in r.get("devices", []) or [] if isinstance(d, dict)]

    # ------------------------------------------------------------ a job
    def send(self, stl_paths: list[str], scene_settings: dict, printer: str, job_name: str, *, supports: str = "auto",
             orient: bool = False, print_now: bool | None = None, save_form: str = "",
             progress: Callable[[str, float], None] | None = None, cancelled=None) -> dict:
        """Prepare and send one job. ``supports``: "auto" (PreForm's supports), "mini_rafts_on_bp" (on the
        build platform) or "none" (models already carry UniSlicer's supports, or print straight on the
        platform). ``printer`` "" with ``save_form`` only saves the .form file. Returns {"scene", "job_id",
        "validation", "print_time_s"}."""
        def step(name, f=0.0):
            if progress:
                progress(name, f)

        step("scene")
        _, scene = self.request("POST", "/scene/", dict(scene_settings))
        sid = scene["id"]
        try:
            for k, path in enumerate(stl_paths):
                step("import", k / max(1, len(stl_paths)))
                self.call("POST", f"/scene/{sid}/import-model/",
                          {"file": os.path.abspath(path), "repair_behavior": "REPAIR", "units": "MILLIMETERS"},
                          cancelled=cancelled)
            if orient:
                step("orient")
                self.call("POST", f"/scene/{sid}/auto-orient/", {"models": "ALL"}, cancelled=cancelled)
            if supports in ("auto", "mini_rafts_on_bp"):
                step("supports")
                body = {"models": "ALL"}
                if supports == "mini_rafts_on_bp":
                    body["raft_type"] = "MINI_RAFTS_ON_BP"
                self.call("POST", f"/scene/{sid}/auto-support/", body, cancelled=cancelled)
            step("layout")
            self.call("POST", f"/scene/{sid}/auto-layout/", {"models": "ALL", "lock_rotation": True},
                      cancelled=cancelled)
            step("check")
            validation = {}
            try:
                validation = self.call("GET", f"/scene/{sid}/print-validation/", cancelled=cancelled)
            except FormlabsError:
                pass
            seconds = 0.0
            try:
                est = self.call("POST", f"/scene/{sid}/estimate-print-time/", cancelled=cancelled)
                seconds = float(est.get("total_print_time_s") or 0.0)
            except FormlabsError:
                pass                               # not every printer type estimates
            if save_form:
                step("save")
                self.call("POST", f"/scene/{sid}/save-form/", {"file": os.path.abspath(save_form)}, cancelled=cancelled)
            job_id = ""
            if printer:
                step("print")
                body = {"printer": printer, "job_name": job_name or "UniSlicer"}
                if print_now is not None:
                    body["print_now"] = bool(print_now)
                r = self.call("POST", f"/scene/{sid}/print/", body, cancelled=cancelled)
                job_id = str(r.get("job_id", ""))
            step("done", 1.0)
            return {"scene": sid, "job_id": job_id, "validation": validation, "print_time_s": seconds}
        finally:
            try:
                self.request("DELETE", f"/scene/{sid}/", timeout=10.0)
            except FormlabsError:
                pass


def validation_problems(validation: dict) -> dict:
    """Totals of PreForm's printability check: {"cups": n, "unsupported_minima": n, "undersupported": n}."""
    out = {"cups": 0, "unsupported_minima": 0, "undersupported": 0}
    for r in (validation or {}).get("per_model_results", {}).values():
        out["cups"] += int(r.get("cups", 0) or 0)
        out["unsupported_minima"] += int(r.get("unsupported_minima", 0) or 0)
        out["undersupported"] += int(bool(r.get("undersupported")))
    return out


def match_printer_type(types: list[dict], product_name: str) -> int:
    """Index of the printer type for a printer's product name ("Form 4B" -> the Form 4 family), or -1."""
    name = (product_name or "").strip().lower()
    for i, t in enumerate(types):
        if any(name == p.lower() for p in t.get("product_names", [])):
            return i
    for i, t in enumerate(types):
        if name and (name.startswith(t["label"].lower()) or t["label"].lower().startswith(name)):
            return i
    return -1

"""Remote control of network printers from anywhere, through the lab's own Firebase project.

How it works (no ports are opened on the router):

- UniSlicer on the computer next to the printers runs an *agent*. It signs in to Firebase as an anonymous
  device account and keeps an outgoing connection to the Realtime Database.
- The owner opens the control page (``remote_web/``) on a phone or any browser and signs in with Google.
- Linking: the agent shows an 8-character code; the owner types it on the page; UniSlicer asks on its own
  screen whether that Google account may control this computer's printers. Only after "Allow" is the account
  the owner of the agent.
- The agent publishes the status of the network printers (state, file, progress) every few seconds and
  carries out the owner's commands (pause / resume / stop) through ``unislicer.network``.

Security: the database rules (``remote_web/database.rules.json``) let only the agent write its own data and
only its owner read it or send commands; printer passwords and API keys never leave this computer.

Standard library only: Firebase Auth (Identity Toolkit + Secure Token REST API) and the Realtime Database
REST API with its Server-Sent Events stream."""
from __future__ import annotations

import json
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from . import network, profiles

STATE_FILE = "remote.json"
BUILTIN_CONFIG = os.path.join(os.path.dirname(__file__), "remote_config.json")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I
CODE_LIFETIME = 15 * 60
STATUS_EVERY = 5.0
OWNER_CHECK_EVERY = 30.0
COMMAND_MAX_AGE = 5 * 60 * 1000                           # ms: older commands are not carried out
AUTH_URL = "https://identitytoolkit.googleapis.com/v1"
TOKEN_URL = "https://securetoken.googleapis.com/v1"


class RemoteError(Exception):
    """A failure the user can act on."""


# ---------------------------------------------------------------- configuration
def parse_config(text: str) -> dict:
    """The Firebase web-app configuration, from JSON or from the ``const firebaseConfig = {...}`` snippet
    that the Firebase console shows (keys may be unquoted, values in single or double quotes)."""
    text = (text or "").strip()
    if not text:
        raise RemoteError("empty")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RemoteError("no_config")
    body = m.group(0)
    try:
        cfg = json.loads(body)
    except ValueError:
        cfg = {k: v for k, _q, v in re.findall(r"""["']?(\w+)["']?\s*:\s*(["'])(.*?)\2""", body)}
    cfg = {k: str(v).strip() for k, v in cfg.items() if isinstance(v, (str, int, float))}
    if not cfg.get("apiKey"):
        raise RemoteError("no_api_key")
    if not cfg.get("databaseURL"):
        if cfg.get("projectId"):           # the console leaves databaseURL out until the database exists
            raise RemoteError("no_database")
        raise RemoteError("no_config")
    cfg["databaseURL"] = cfg["databaseURL"].rstrip("/")
    if not cfg["databaseURL"].startswith(("https://", "http://")):
        raise RemoteError("no_database")
    return cfg


def builtin_config() -> dict | None:
    """A configuration shipped with UniSlicer (remote_config.json), so users don't need their own project."""
    try:
        with open(BUILTIN_CONFIG, "r", encoding="utf-8") as fh:
            return parse_config(fh.read())
    except (OSError, RemoteError):
        return None


def _state_path(folder: str | None = None) -> str:
    return os.path.join(folder or profiles.USER_DIR, STATE_FILE)


def load_state(folder: str | None = None) -> dict:
    try:
        with open(_state_path(folder), "r", encoding="utf-8") as fh:
            st = json.load(fh)
            return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(st: dict, folder: str | None = None) -> None:
    path = _state_path(folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def new_code(n: int = 8) -> str:
    rnd = random.SystemRandom()
    return "".join(rnd.choice(CODE_ALPHABET) for _ in range(n))


def pretty_code(code: str) -> str:
    return code[:4] + "-" + code[4:] if len(code) == 8 else code


# ---------------------------------------------------------------- Firebase REST
def _request(url: str, method: str = "GET", data=None, headers=None, timeout: float = 20.0):
    body = None if data is None else json.dumps(data).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read() if hasattr(e, "read") else b""
        try:
            msg = json.loads(raw.decode() or "{}")
            msg = (msg.get("error") or {}).get("message") if isinstance(msg.get("error"), dict) else msg.get("error")
        except ValueError:
            msg = raw[:200].decode("utf-8", "replace")
        if e.code in (401, 403):
            raise RemoteError(f"denied: {msg or e.code}") from e
        raise RemoteError(f"HTTP {e.code}: {msg}") from e
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise RemoteError(f"offline: {getattr(e, 'reason', e)}") from e
    try:
        return json.loads(raw.decode() or "null")
    except ValueError:
        return None


class Firebase:
    """A signed-in device account and the Realtime Database, over REST."""

    def __init__(self, cfg: dict, refresh_token: str = "", auth_url: str = AUTH_URL, token_url: str = TOKEN_URL):
        self.cfg = cfg
        self.db = cfg["databaseURL"]
        self.key = cfg["apiKey"]
        self.auth_url = cfg.get("authURL", auth_url).rstrip("/")
        self.token_url = cfg.get("tokenURL", token_url).rstrip("/")
        self.refresh_token = refresh_token
        self.uid = ""
        self._id_token = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    # ---- auth
    def sign_in(self) -> str:
        """Use the saved device account, or create one (Anonymous sign-in must be on in the project)."""
        if self.refresh_token:
            try:
                self._refresh()
                return self.uid
            except RemoteError as e:
                if not str(e).startswith(("denied", "HTTP 400")):
                    raise
        r = _request(f"{self.auth_url}/accounts:signUp?key={self.key}", "POST", {"returnSecureToken": True})
        if not isinstance(r, dict) or "idToken" not in r:
            raise RemoteError("auth_failed")
        self.uid, self._id_token, self.refresh_token = r["localId"], r["idToken"], r["refreshToken"]
        self._expires = time.time() + float(r.get("expiresIn", 3600)) - 120
        return self.uid

    def _refresh(self):
        r = _request(f"{self.token_url}/token?key={self.key}", "POST",
                     {"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        if not isinstance(r, dict) or "id_token" not in r:
            raise RemoteError("auth_failed")
        self.uid, self._id_token = r["user_id"], r["id_token"]
        self.refresh_token = r.get("refresh_token", self.refresh_token)
        self._expires = time.time() + float(r.get("expires_in", 3600)) - 120

    def token(self) -> str:
        with self._lock:
            if not self._id_token or time.time() > self._expires:
                self._refresh() if self.refresh_token else self.sign_in()
            return self._id_token

    # ---- database
    def url(self, path: str) -> str:
        path = "/".join(urllib.parse.quote(p, safe="") for p in path.strip("/").split("/") if p)
        return f"{self.db}/{path}.json?auth={urllib.parse.quote(self.token(), safe='')}"

    def get(self, path):
        return _request(self.url(path))

    def put(self, path, value):
        return _request(self.url(path), "PUT", value)

    def patch(self, path, value):
        return _request(self.url(path), "PATCH", value)

    def delete(self, path):
        return _request(self.url(path), "DELETE")

    def stream(self, path: str, on_event: Callable[[str, dict], None], stop: threading.Event,
               timeout: float = 75.0) -> None:
        """Follow a database location (Server-Sent Events) until ``stop`` is set; reconnects on drops
        and on token expiry. ``on_event(event, {"path", "data"})`` gets 'put' and 'patch' events."""
        delay = 1.0
        while not stop.is_set():
            try:
                req = urllib.request.Request(self.url(path), headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    delay = 1.0
                    event = ""
                    for raw in r:
                        if stop.is_set():
                            return
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            if event in ("put", "patch"):
                                try:
                                    on_event(event, json.loads(line[5:].strip()))
                                except ValueError:
                                    pass
                            elif event in ("auth_revoked", "cancel"):
                                with self._lock:
                                    self._id_token = ""
                                break
            except (urllib.error.URLError, socket.timeout, ConnectionError, OSError, RemoteError):
                pass
            stop.wait(delay)
            delay = min(delay * 2, 30.0)


SERVER_TIME = {".sv": "timestamp"}


# ---------------------------------------------------------------- the agent
class Agent:
    """Runs in background threads; reports through callbacks (called from those threads):
    ``on_state(state, detail)`` with state in idle / connecting / pairing / linked / error, and
    ``on_claim(email) -> None`` when a Google account asks to link (answer with ``answer_claim``)."""

    def __init__(self, cfg: dict, folder: str | None = None, name: str = "", on_state=None, on_claim=None,
                 on_command=None, printers=None):
        self.cfg = cfg
        self.folder = folder
        self.st = load_state(folder)
        self.name = name or self.st.get("name") or socket.gethostname() or "UniSlicer"
        self.on_state = on_state or (lambda s, d: None)
        self.on_claim = on_claim or (lambda email: None)
        self.on_command = on_command or (lambda cmd, ok, msg: None)
        self.printers = printers or (lambda: network.load_printers(folder))
        self.fb: Firebase | None = None
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.state, self.detail = "idle", ""
        self.code = ""
        self._claim: dict | None = None
        self._claim_answer = threading.Event()
        self._claim_ok = False
        self._last_status: dict = {}
        self._session = threading.Event()
        self._done: set[str] = set()
        self._done_lock = threading.Lock()

    # ---- lifecycle
    @property
    def owner_email(self) -> str:
        return self.st.get("owner_email", "")

    def running(self) -> bool:
        return any(t.is_alive() for t in self.threads)

    def start(self):
        if self.running():
            return
        self.stop_event.clear()
        t = threading.Thread(target=self._main, name="remote-agent", daemon=True)
        self.threads = [t]
        t.start()

    def stop(self, wait: float = 3.0):
        self.stop_event.set()
        self._session.set()
        self._claim_answer.set()
        fb = self.fb
        if fb is not None and fb.uid and self.st.get("owner"):
            try:
                fb.patch(f"agents/{fb.uid}/info", {"online": False, "seen": SERVER_TIME})
            except RemoteError:
                pass
        for t in self.threads:
            t.join(wait)
        self._set("idle", "")

    def unlink(self):
        """Forget the owner: removes this computer from the account and from the database."""
        fb = self.fb
        self.stop(1.0)
        if fb is not None and fb.uid:
            try:
                fb.delete(f"agents/{fb.uid}")
            except RemoteError:
                pass
        st = load_state(self.folder)
        for k in ("owner", "owner_email", "refresh_token", "uid"):
            self.st.pop(k, None)
            st.pop(k, None)
        save_state(st, self.folder)
        self.fb = None

    def answer_claim(self, allow: bool):
        self._claim_ok = bool(allow)
        self._claim_answer.set()

    def _set(self, state, detail=""):
        self.state, self.detail = state, detail
        try:
            self.on_state(state, detail)
        except Exception:
            pass

    def _save(self):
        """Store the device account and owner, keeping what else the file holds (configuration, choices)."""
        st = load_state(self.folder)
        for k in ("owner", "owner_email"):
            if self.st.get(k):
                st[k] = self.st[k]
            else:
                st.pop(k, None)
        st["name"] = self.name
        if self.fb is not None:
            st["refresh_token"] = self.fb.refresh_token
            st["uid"] = self.fb.uid
        save_state(st, self.folder)

    # ---- main loop
    def _main(self):
        delay = 2.0
        while not self.stop_event.is_set():
            try:
                self._set("connecting")
                self.fb = Firebase(self.cfg, self.st.get("refresh_token", ""))
                self.fb.sign_in()
                self._save()
                if not self.st.get("owner") or not self._still_owned():
                    if not self._pair():
                        continue
                self._linked()
                if self.stop_event.is_set():
                    return
                delay = 2.0
                continue
            except RemoteError as e:
                self._set("error", str(e))
            except Exception as e:                     # never let the agent take the program down
                self._set("error", f"{type(e).__name__}: {e}")
            self.stop_event.wait(delay)
            delay = min(delay * 2, 60.0)

    def _still_owned(self) -> bool:
        owner = self.fb.get(f"agents/{self.fb.uid}/owner")
        if owner and owner == self.st.get("owner"):
            return True
        self.st.pop("owner", None)
        self.st.pop("owner_email", None)
        self._save()
        return False

    def _pair(self) -> bool:
        """Show a code and wait for the owner to type it on the control page; True once linked."""
        fb = self.fb
        while not self.stop_event.is_set():
            self.code = new_code()
            fb.put(f"pairing/{self.code}", {"agent": fb.uid, "name": self.name, "created": SERVER_TIME})
            self._set("pairing", self.code)
            self._claim = None
            got = threading.Event()

            def on_event(_ev, msg):
                d = msg.get("data")
                if msg.get("path") == "/" and isinstance(d, dict) and isinstance(d.get("claim"), dict):
                    self._claim = d["claim"]
                    got.set()
                elif msg.get("path") == "/claim" and isinstance(d, dict):
                    self._claim = d
                    got.set()

            stop = threading.Event()
            t = threading.Thread(target=fb.stream, args=(f"pairing/{self.code}", on_event, stop), daemon=True)
            t.start()
            end = time.time() + CODE_LIFETIME
            while not got.is_set() and not self.stop_event.is_set() and time.time() < end:
                got.wait(0.5)
            stop.set()
            claim = self._claim
            try:
                fb.delete(f"pairing/{self.code}")
            except RemoteError:
                pass
            self.code = ""
            if self.stop_event.is_set():
                return False
            if not claim or not claim.get("uid"):
                continue                                # code expired: show a new one
            self._claim_answer.clear()
            self._claim_ok = False
            self._set("confirm", claim.get("email", ""))
            try:
                self.on_claim(claim.get("email", ""))
            except Exception:
                pass
            self._claim_answer.wait(300)
            if self.stop_event.is_set():
                return False
            if not self._claim_ok:
                continue
            fb.patch(f"agents/{fb.uid}", {"owner": claim["uid"], "owner_email": claim.get("email", ""),
                                          "info": {"name": self.name, "online": True, "seen": SERVER_TIME,
                                                   "app": "UniSlicer"}})
            self.st["owner"], self.st["owner_email"] = claim["uid"], claim.get("email", "")
            self._save()
            return True
        return False

    def _linked(self):
        """Publish status and carry out commands until stopped or the owner removes this computer."""
        fb = self.fb
        fb.patch(f"agents/{fb.uid}/info", {"name": self.name, "online": True, "seen": SERVER_TIME,
                                           "app": "UniSlicer"})
        self._set("linked", self.owner_email)
        self._session = threading.Event()
        status = threading.Thread(target=self._status_loop, args=(self._session,), name="remote-status",
                                  daemon=True)
        status.start()
        self.threads.append(status)
        fb.stream(f"agents/{fb.uid}/commands", self._on_commands, self._session)
        status.join(5.0)

    # ---- printers
    def publish_status(self):
        fb = self.fb
        out = {}
        for p in self.printers():
            row = {"name": p.name, "kind": p.kind, "control": network.can_control(p.kind)}
            try:
                s = network.printer_status(p)
                row.update({"state": str(s.get("state") or ""), "file": str(s.get("file") or ""),
                            "percent": s.get("percent"), "layer": s.get("layer"), "layers": s.get("layers"),
                            "online": True})
            except Exception as e:
                row.update({"state": "offline", "online": False, "error": str(e)[:160]})
            out[p.id] = {k: v for k, v in row.items() if v is not None}
        if out != self._last_status:
            fb.put(f"agents/{fb.uid}/printers", out or None)
            self._last_status = out
        fb.patch(f"agents/{fb.uid}/info", {"online": True, "seen": SERVER_TIME})

    def _status_loop(self, session: threading.Event):
        checked = 0.0
        while not self.stop_event.is_set() and not session.is_set():
            try:
                self.publish_status()
                if time.time() - checked > OWNER_CHECK_EVERY:      # removed from the control page?
                    checked = time.time()
                    if not self._still_owned():
                        self._set("unlinked", "")
                        break
            except RemoteError:
                pass
            except Exception:
                pass
            self.stop_event.wait(STATUS_EVERY)
        session.set()

    def _on_commands(self, _ev, msg):
        data, path = msg.get("data"), msg.get("path", "/")
        if data is None:
            return
        cmds = data if path == "/" else {path.strip("/").split("/")[0]: data}
        if not isinstance(cmds, dict):
            return
        for cid, cmd in cmds.items():
            if isinstance(cmd, dict):
                threading.Thread(target=self.run_command, args=(cid, cmd), daemon=True).start()

    def run_command(self, cid: str, cmd: dict):
        with self._done_lock:                          # a reconnecting stream repeats pending commands
            if cid in self._done:
                return
            self._done.add(cid)
        fb = self.fb
        try:
            fb.delete(f"agents/{fb.uid}/commands/{cid}")
        except RemoteError:
            return
        action, pid = cmd.get("action"), cmd.get("printer")
        if not isinstance(pid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", pid):
            pid = ""
        ok, msg = False, ""
        at = cmd.get("at")
        try:
            if isinstance(at, (int, float)) and abs(time.time() * 1000 - at) > COMMAND_MAX_AGE:
                raise network.NetworkError("expired: the command is more than 5 minutes old")
            if action == "refresh":
                self._last_status = {}
                self.publish_status()
            else:
                p = next((q for q in self.printers() if q.id == pid), None)
                if p is None:
                    raise network.NetworkError("printer not found on this computer")
                network.control(p, action)
                self._last_status = {}
            ok = True
        except Exception as e:
            msg = str(e)[:200]
        try:
            fb.put(f"agents/{fb.uid}/results/{pid or 'agent'}", {"id": cid, "ok": ok, "message": msg, "action": action or "",
                                                       "printer": pid or "", "at": SERVER_TIME})
        except RemoteError:
            pass
        try:
            self.on_command(cmd, ok, msg)
        except Exception:
            pass
        if ok and action != "refresh":
            time.sleep(1.0)
            try:
                self.publish_status()
            except Exception:
                pass


def explain(err: str, lang: str = "he") -> str:
    """A remote-control failure in plain words."""
    he = lang == "he"
    table = {
        "empty": ("הדבק את הגדרות ה-Firebase.", "Paste the Firebase configuration."),
        "no_config": ("לא נמצאו הגדרות. העתק את כל הקטע firebaseConfig = {...} מהגדרות הפרויקט ב-Firebase.",
                      "No configuration found. Copy the whole firebaseConfig = {...} block from the Firebase project settings."),
        "no_api_key": ("בהגדרות חסר apiKey.", "The configuration has no apiKey."),
        "no_database": ("בהגדרות חסר databaseURL: צור Realtime Database בפרויקט (Build › Realtime Database), ואז "
                        "העתק שוב את ההגדרות.",
                        "The configuration has no databaseURL: create a Realtime Database in the project (Build › "
                        "Realtime Database), then copy the configuration again."),
        "auth_failed": ("ההתחברות ל-Firebase נכשלה. בדוק שהפעלת Anonymous ו-Google ב-Authentication › Sign-in method.",
                        "Signing in to Firebase failed. Check that Anonymous and Google are on in Authentication › Sign-in method."),
    }
    if err in table:
        return table[err][0 if he else 1]
    if err.startswith("offline"):
        return ("אין חיבור לאינטרנט או ש-Firebase לא זמין. ננסה שוב אוטומטית." if he else
                "No internet connection or Firebase is unreachable. Retrying automatically.") + f"\n({err})"
    if err.startswith("denied"):
        if "ADMIN_ONLY_OPERATION" in err or "OPERATION_NOT_ALLOWED" in err:
            return table["auth_failed"][0 if he else 1]
        return ("Firebase דחה את הגישה: פרסם את חוקי האבטחה מהקובץ database.rules.json (Realtime Database › Rules), "
                "או שהמחשב נותק מהחשבון בדף השליטה." if he else
                "Firebase denied access: publish the rules from database.rules.json (Realtime Database › Rules), "
                "or this computer was unlinked from the control page.") + f"\n({err})"
    if "OPERATION_NOT_ALLOWED" in err or "ADMIN_ONLY" in err:
        return table["auth_failed"][0 if he else 1]
    return err

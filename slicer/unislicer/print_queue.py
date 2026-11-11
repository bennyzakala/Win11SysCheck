"""Print queue for the network printers (as GrabCAD Print's Schedule view): each printer has an ordered
list of prepared print files. A job is sent - and started - only when the user says so, because the plate
has to be emptied between prints; the queue keeps the order, the estimates and what happened to each job."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

from . import profiles

FILE_NAME = "print_queue.json"
STATES = ("waiting", "sending", "sent", "failed")
IDLE_STATES = {"idle", "ready", "complete", "completed", "finished", "standby", "operational", "stopped", "cancelled"}


@dataclass
class Job:
    printer: str                       # NetPrinter.id
    path: str                          # the print file (in the prints folder)
    name: str = ""
    time_s: float = 0.0                # estimated print time
    material: str = ""                 # e.g. "12.4 ml"
    state: str = "waiting"
    note: str = ""                     # last error or where it was sent
    added: float = field(default_factory=time.time)
    sent: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])


def printer_ready(status: dict | None) -> bool:
    """A printer can take the next job when it reports an idle / finished state."""
    if not status:
        return False
    st = str(status.get("state", "")).strip().lower()
    return st in IDLE_STATES


_STARTED: set = set()           # queue files already loaded by this run of the program
_SHARED: dict = {}


class Queue:
    def __init__(self, folder: str | None = None):
        self.folder = folder or profiles.USER_DIR
        self.path = os.path.join(self.folder, FILE_NAME)
        self.jobs: list[Job] = []
        self.load()

    @classmethod
    def shared(cls, folder: str | None = None) -> "Queue":
        """The one queue the whole program works on: every queue window and the window's exports use it.
        A queue window reopened during an upload made its own copy from the file - it offered the job being
        sent as waiting (sent twice) and, when the old window's upload ended, its save dropped the new jobs."""
        key = os.path.normcase(os.path.abspath(os.path.join(folder or profiles.USER_DIR, FILE_NAME)))
        q = _SHARED.get(key)
        if q is None:
            q = _SHARED[key] = cls(folder)
        return q

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                rows = json.load(fh)
        except (OSError, ValueError):
            rows = []
        self.jobs = []
        key = os.path.normcase(os.path.abspath(self.path))
        first = key not in _STARTED
        _STARTED.add(key)
        for r in rows if isinstance(rows, list) else []:
            try:
                job = Job(**{k: v for k, v in r.items() if k in Job.__dataclass_fields__})
            except TypeError:
                continue
            if job.state == "sending" and first:   # interrupted (app closed while uploading): try again -
                job.state = "waiting"                  # only at start; later "sending" is an upload running now
            self.jobs.append(job)

    def save(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump([asdict(j) for j in self.jobs], fh, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------- editing
    def add(self, printer: str, path: str, name: str = "", time_s: float = 0.0, material: str = "") -> Job:
        job = Job(printer, path, name or os.path.basename(path), float(time_s), material)
        self.jobs.append(job)
        self.save()
        return job

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def remove(self, job_id: str) -> None:
        self.jobs = [j for j in self.jobs if j.id != job_id]
        self.save()

    def for_printer(self, printer: str, waiting_only: bool = False) -> list[Job]:
        return [j for j in self.jobs if j.printer == printer and (not waiting_only or j.state == "waiting")]

    def reorder(self, printer: str, job_ids: list[str]) -> None:
        """Set the order of one printer's jobs (as dragged in the list); other printers keep theirs."""
        mine = {j.id: j for j in self.jobs if j.printer == printer}
        ordered = [mine[i] for i in job_ids if i in mine] + [j for i, j in mine.items() if i not in job_ids]
        it = iter(ordered)
        self.jobs = [next(it) if j.printer == printer else j for j in self.jobs]
        self.save()

    def move_to(self, job_id: str, printer: str) -> None:
        job = self.get(job_id)
        if job is not None:
            job.printer = printer
            job.state = "waiting" if job.state != "sent" else job.state
            self.jobs.remove(job)
            self.jobs.append(job)
            self.save()

    def next_for(self, printer: str) -> Job | None:
        return next((j for j in self.jobs if j.printer == printer and j.state == "waiting"), None)

    def mark(self, job_id: str, state: str, note: str = "") -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.state, job.note = state, note
        if state == "sent":
            job.sent = time.time()
        self.save()

    def clear_sent(self) -> None:
        self.jobs = [j for j in self.jobs if j.state != "sent"]
        self.save()

    def waiting_time(self, printer: str) -> float:
        return sum(j.time_s for j in self.for_printer(printer, waiting_only=True))

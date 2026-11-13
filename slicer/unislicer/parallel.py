"""A shared process pool for the CPU-heavy, per-layer work (slicing, toolpaths, layer images)."""
from __future__ import annotations

import multiprocessing
import os
import sys
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Callable, Iterable, Sequence

_pool: ProcessPoolExecutor | None = None
_disabled = False   # set when the pool cannot start (then everything runs in-process)
_lock = threading.Lock()


def workers() -> int:
    """Worker count; UNISLICER_WORKERS overrides it (1 disables multiprocessing)."""
    env = os.environ.get("UNISLICER_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, min(8, (os.cpu_count() or 1)))


def _watch_parent() -> None:
    """In each worker process: end it as soon as the program that started it has ended - however it ended
    (closed, updated, crashed, ended in Task Manager). Workers left running after the program closed were
    seen at the lab: ~25 UniSlicer processes in Task Manager, a dozen still busy."""
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch():
        try:
            parent.join()                # returns when the program's process has ended
        except Exception:
            pass
        os._exit(0)
    threading.Thread(target=watch, name="unislicer-parent-watch", daemon=True).start()


_final = False       # the program is ending: no worker processes are started any more


def pool() -> ProcessPoolExecutor | None:
    """The shared pool, started on first use (None when running single-process)."""
    global _pool
    if workers() < 2 or _disabled or _final:
        return None
    with _lock:
        if _pool is None:
            # "spawn" everywhere: forking a process that runs Qt threads is unsafe
            _pool = ProcessPoolExecutor(max_workers=workers(), mp_context=multiprocessing.get_context("spawn"),
                                        initializer=_watch_parent)
        return _pool


def shutdown() -> None:
    _discard()


def terminate(final: bool = False) -> None:
    """Stop the worker processes now, whatever they are doing: when the program closes (or updates).
    A plain shutdown let them finish every job already handed to them - a full plate's pre-slice or
    support builds ran on for minutes at full CPU after the window had closed (the lab saw 13 UniSlicer
    processes in Task Manager), and Python waits for them before it ends."""
    global _pool, _stops, _final
    with _lock:
        old, _pool = _pool, None
        _stops += 1
        if final:                    # the program itself is ending (not a window replaced): no new workers
            _final = True                  # jobs started before this do not start new workers: they end, cancelled
    if old is None:
        return
    procs = list((getattr(old, "_processes", None) or {}).values())
    try:
        old.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    _end(procs)


def chunk_ranges(n: int, min_chunk: int, max_chunks: int) -> list[tuple[int, int]]:
    """Split range(n) into contiguous (start, end) pieces of at least ``min_chunk`` items."""
    count = max(1, min(max_chunks, n // max(1, min_chunk)))
    bounds = [round(i * n / count) for i in range(count + 1)]
    return [(bounds[i], bounds[i + 1]) for i in range(count) if bounds[i + 1] > bounds[i]]


def run_chunks(fn: Callable, args: Sequence[tuple], progress: Callable[[float], None] | None = None,
               cancelled: Callable[[], bool] | None = None, weights: Iterable[float] | None = None) -> list:
    """Run ``fn(*a)`` for each tuple in ``args``, in parallel when possible; results keep the input order.
    A pool that broke (a worker died) or was shut down is replaced once; after that the work runs on one core,
    so a job never fails because of the pool."""
    global _disabled
    stops = _stops
    for attempt in range(2):
        try:
            return _run(fn, args, progress, cancelled, weights)
        except (BrokenProcessPool, RuntimeError) as exc:
            if _stops != stops:
                # the workers were ended on purpose (the program closing or updating): no new ones for this job
                raise InterruptedError from exc
            # a broken pool also marks itself shut down, so a submit racing it raises
            # RuntimeError("cannot schedule new futures after shutdown")
            if isinstance(exc, RuntimeError) and not isinstance(exc, BrokenProcessPool) and "shutdown" not in str(exc):
                raise
            _discard()
            print(f"process pool lost ({exc}); " + ("restarting it" if attempt == 0 else "continuing on one core"),
                  file=sys.stderr)
    _disabled = True
    return _run(fn, args, progress, cancelled, weights)


def _discard() -> None:
    """Drop the current pool (the next use starts a fresh one); its worker processes are ended (a pool given
    up as stuck has workers that would never end by themselves)."""
    global _pool
    with _lock:
        old, _pool = _pool, None
    if old is not None:
        procs = list((getattr(old, "_processes", None) or {}).values())
        try:
            old.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _end(procs)


def _end(procs) -> None:
    """End worker processes and wait for them; one that does not end within 2 s is killed."""
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    for p in procs:
        try:
            p.join(2.0)
            if p.is_alive():
                p.kill()
                p.join(2.0)
        except Exception:
            pass


def hang_limit() -> float:
    """Seconds without a single result from the worker processes before the pool is given up as stuck."""
    try:
        return max(1.0, float(os.environ.get("UNISLICER_HANG_LIMIT", "180")))
    except ValueError:
        return 180.0


def _run(fn, args, progress, cancelled, weights) -> list:
    weights = list(weights) if weights is not None else [1.0] * len(args)
    total = sum(weights) or 1.0
    ex = pool() if len(args) > 1 else None
    results: list = [None] * len(args)
    done = 0.0
    if ex is None:
        for i, a in enumerate(args):
            if cancelled and cancelled():
                raise InterruptedError
            results[i] = fn(*a)
            done += weights[i]
            if progress:
                progress(done / total)
        return results
    global _active
    with _lock:
        _active += 1
    try:
        return _run_in(ex, fn, args, progress, cancelled, weights, total, results)
    finally:
        with _lock:
            _active -= 1


_active = 0          # runs using the pool now
_stops = 0           # times the workers were ended on purpose (terminate)


def busy() -> bool:
    """Is the pool working for someone (a slice, a pre-slice, an export)? A quick job asked for meanwhile
    would wait behind all of that work (seconds to minutes on a full plate): it is better done here."""
    return _active > 0


def _run_in(ex, fn, args, progress, cancelled, weights, total, results) -> list:
    import time
    done = 0.0
    futures: dict[Future, int] = {ex.submit(fn, *a): i for i, a in enumerate(args)}
    pending = set(futures)
    last = time.monotonic()
    limit = hang_limit()
    try:
        from concurrent.futures import FIRST_COMPLETED, wait
        while pending:
            finished, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            if cancelled and cancelled():
                raise InterruptedError
            if finished:
                last = time.monotonic()
            elif time.monotonic() - last > limit:
                # Windows, now and then: the jobs sit in the pipe to the workers and nothing comes back - a
                # support build "stuck" for good. The pool is given up (its workers ended) and the work redone
                raise BrokenProcessPool(f"no result from the worker processes for {limit:g} s")
            for f in finished:
                i = futures[f]
                results[i] = f.result()
                done += weights[i]
                if progress:
                    progress(done / total)
    except BaseException:
        for f in pending:
            f.cancel()
        raise
    return results


def _noop(i):
    from . import sla, slicing  # noqa: F401  (load numpy / shapely / the slicer in each worker now)
    return i


def prewarm() -> None:
    """Start every worker process now (each imports the slicer once), so the first real job does not wait."""
    n = workers()
    if n >= 2 and not _disabled:
        run_chunks(_noop, [(i,) for i in range(n)])

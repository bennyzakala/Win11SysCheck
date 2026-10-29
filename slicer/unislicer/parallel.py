"""A shared process pool for the CPU-heavy, per-layer work (slicing, toolpaths, layer images)."""
from __future__ import annotations

import multiprocessing
import os
import sys
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Callable, Iterable, Sequence

_pool: ProcessPoolExecutor | None = None
_disabled = False   # set when the pool cannot start (then everything runs in-process)


def workers() -> int:
    """Worker count; UNISLICER_WORKERS overrides it (1 disables multiprocessing)."""
    env = os.environ.get("UNISLICER_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, min(8, (os.cpu_count() or 1)))


def pool() -> ProcessPoolExecutor | None:
    """The shared pool, started on first use (None when running single-process)."""
    global _pool
    if workers() < 2 or _disabled:
        return None
    if _pool is None:
        # "spawn" everywhere: forking a process that runs Qt threads is unsafe
        _pool = ProcessPoolExecutor(max_workers=workers(), mp_context=multiprocessing.get_context("spawn"))
    return _pool


def shutdown() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def chunk_ranges(n: int, min_chunk: int, max_chunks: int) -> list[tuple[int, int]]:
    """Split range(n) into contiguous (start, end) pieces of at least ``min_chunk`` items."""
    count = max(1, min(max_chunks, n // max(1, min_chunk)))
    bounds = [round(i * n / count) for i in range(count + 1)]
    return [(bounds[i], bounds[i + 1]) for i in range(count) if bounds[i + 1] > bounds[i]]


def run_chunks(fn: Callable, args: Sequence[tuple], progress: Callable[[float], None] | None = None,
               cancelled: Callable[[], bool] | None = None, weights: Iterable[float] | None = None) -> list:
    """Run ``fn(*a)`` for each tuple in ``args``, in parallel when possible; results keep the input order."""
    try:
        return _run(fn, args, progress, cancelled, weights)
    except BrokenProcessPool as exc:
        # e.g. a worker could not start: never fail the job because of the pool
        global _disabled
        _disabled = True
        shutdown()
        print(f"process pool unavailable ({exc}); continuing on one core", file=sys.stderr)
        return _run(fn, args, progress, cancelled, weights)


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
    futures: dict[Future, int] = {ex.submit(fn, *a): i for i, a in enumerate(args)}
    pending = set(futures)
    try:
        from concurrent.futures import FIRST_COMPLETED, wait
        while pending:
            finished, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            if cancelled and cancelled():
                raise InterruptedError
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

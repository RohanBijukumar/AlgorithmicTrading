"""Bounded background jobs with incremental activity and coalesced daily metrics."""

from collections import deque
from threading import Event, Lock
from time import time
from uuid import uuid4


class JobStore:
    def __init__(self, max_active=2):
        self.lock = Lock()
        self.jobs = {}
        self.max_active = max_active

    def create(self, total=0):
        with self.lock:
            if sum(j["status"] == "running" for j in self.jobs.values()) >= self.max_active:
                raise ValueError("A job is already running. Wait for it to finish or cancel it.")
            finished = [key for key, job in self.jobs.items() if job["status"] != "running"]
            for key in finished[:-30]:
                del self.jobs[key]
            key = uuid4().hex
            self.jobs[key] = {
                "status": "running",
                "events": deque(maxlen=2000),
                "cursor": 0,
                "latest": None,
                "points": [],
                "result": None,
                "error": None,
                "cancel": Event(),
                "started_at": time(),
                "total": total,
                "completed": 0,
            }
            return key

    def emit(self, key, event):
        with self.lock:
            job = self.jobs[key]
            if event.get("type") == "daily_return":
                job["latest"] = event
                job["points"].append(
                    {k: event[k] for k in ("date", "cash", "market_value", "total_value")}
                )
            else:
                job["cursor"] += 1
                job["events"].append({**event, "sequence": job["cursor"]})
            if event.get("status") in ("covered", "completed", "failed"):
                job["completed"] += 1

    def update(self, key, **kwargs):
        with self.lock:
            self.jobs[key].update(kwargs)

    def cancelled(self, key):
        with self.lock:
            return self.jobs[key]["cancel"].is_set()

    def cancel(self, key):
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise LookupError("job not found")
            if job["status"] == "running":
                job["cancel"].set()

    def snapshot(self, key, after=0):
        with self.lock:
            job = self.jobs.get(key)
            if not job:
                raise LookupError("job not found; the server may have restarted")
            points = job["points"]
            if len(points) > 260:
                points = [points[round(i * (len(points) - 1) / 259)] for i in range(260)]
            return {k: v for k, v in job.items() if k not in {"cancel", "events", "points"}} | {
                "events": [event for event in job["events"] if event["sequence"] > after],
                "points": list(points),
                "elapsed_seconds": time() - job["started_at"],
                "cancel_requested": job["cancel"].is_set(),
            }

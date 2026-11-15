"""AOS scheduler framework.

Pure-Python discrete-time simulator used in MS Advanced Operating Systems.
Supports single-core policy comparison and a lightweight multicore queue/affinity model.
No third-party packages are required.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from collections import deque
from typing import Dict, List, Optional, Iterable, Tuple
import csv
import json
import random
import statistics


@dataclass
class Job:
    jid: str
    arrival: int
    burst: int
    priority: int = 0   # lower number = higher priority
    tickets: int = 1
    affinity: Optional[int] = None

    def __post_init__(self):
        if self.arrival < 0 or self.burst <= 0:
            raise ValueError("arrival must be >=0 and burst must be >0")
        if self.tickets <= 0:
            raise ValueError("tickets must be >0")


@dataclass
class JobResult:
    jid: str
    arrival: int
    burst: int
    first_start: int
    completion: int
    response: int
    turnaround: int
    waiting: int
    migrations: int = 0


@dataclass
class SimulationResult:
    policy: str
    jobs: List[JobResult]
    makespan: int
    context_switches: int
    timeline: List[Tuple[int, str]] = field(default_factory=list)

    def summary(self) -> Dict[str, float]:
        return {
            "policy": self.policy,
            "jobs": len(self.jobs),
            "makespan": self.makespan,
            "avg_response": round(statistics.mean(j.response for j in self.jobs), 4),
            "avg_turnaround": round(statistics.mean(j.turnaround for j in self.jobs), 4),
            "avg_waiting": round(statistics.mean(j.waiting for j in self.jobs), 4),
            "throughput": round(len(self.jobs) / self.makespan, 6) if self.makespan else 0.0,
            "context_switches": self.context_switches,
        }


def load_jobs_csv(path: str) -> List[Job]:
    jobs: List[Job] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            jobs.append(Job(
                jid=r["jid"],
                arrival=int(r["arrival"]),
                burst=int(r["burst"]),
                priority=int(r.get("priority") or 0),
                tickets=int(r.get("tickets") or 1),
                affinity=(None if not r.get("affinity") else int(r["affinity"])),
            ))
    return jobs


def save_summary_csv(rows: Iterable[Dict[str, object]], path: str) -> None:
    rows = list(rows)
    if not rows:
        raise ValueError("no rows to save")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _finalize(policy: str, jobs: List[Job], first: Dict[str, int], completion: Dict[str, int],
              context_switches: int, timeline: List[Tuple[int, str]], migrations: Optional[Dict[str, int]] = None) -> SimulationResult:
    migrations = migrations or {}
    results: List[JobResult] = []
    for j in jobs:
        turnaround = completion[j.jid] - j.arrival
        waiting = turnaround - j.burst
        results.append(JobResult(
            jid=j.jid, arrival=j.arrival, burst=j.burst,
            first_start=first[j.jid], completion=completion[j.jid],
            response=first[j.jid] - j.arrival,
            turnaround=turnaround, waiting=waiting,
            migrations=migrations.get(j.jid, 0),
        ))
    return SimulationResult(policy, results, max(completion.values()) if completion else 0,
                            context_switches, timeline)


def simulate_single_core(jobs: List[Job], policy: str, quantum: int = 2, seed: int = 7) -> SimulationResult:
    """Simulate FCFS, SJF, SRTF, RR, PRIORITY, or LOTTERY on one core.

    Time is integer ticks; a selected job executes for one tick. RR uses the supplied quantum.
    Priority is preemptive and lower numeric priority wins.
    Lottery redraws each tick using job ticket weights.
    """
    policy = policy.upper()
    supported = {"FCFS", "SJF", "SRTF", "RR", "PRIORITY", "LOTTERY"}
    if policy not in supported:
        raise ValueError(f"unsupported policy {policy}; choose {sorted(supported)}")
    if quantum <= 0:
        raise ValueError("quantum must be >0")
    jobs = sorted([Job(**asdict(j)) for j in jobs], key=lambda j: (j.arrival, j.jid))
    remaining = {j.jid: j.burst for j in jobs}
    first: Dict[str, int] = {}
    completion: Dict[str, int] = {}
    t = 0
    current: Optional[str] = None
    last_running: Optional[str] = None
    context_switches = 0
    timeline: List[Tuple[int, str]] = []
    rr_q = 0
    rr_queue: deque[str] = deque()
    admitted = set()
    rng = random.Random(seed)

    while len(completion) < len(jobs):
        arrivals = [j for j in jobs if j.arrival <= t and j.jid not in admitted]
        for j in arrivals:
            admitted.add(j.jid)
            if policy == "RR":
                rr_queue.append(j.jid)

        ready_jobs = [j for j in jobs if j.arrival <= t and remaining[j.jid] > 0]
        if not ready_jobs:
            timeline.append((t, "IDLE"))
            t += 1
            current = None
            rr_q = 0
            continue

        # Choose next job.
        if policy == "FCFS":
            if current is None or remaining[current] <= 0:
                candidates = sorted(ready_jobs, key=lambda j: (j.arrival, j.jid))
                current = candidates[0].jid
        elif policy == "SJF":
            if current is None or remaining[current] <= 0:
                current = min(ready_jobs, key=lambda j: (j.burst, j.arrival, j.jid)).jid
        elif policy == "SRTF":
            current = min(ready_jobs, key=lambda j: (remaining[j.jid], j.arrival, j.jid)).jid
        elif policy == "PRIORITY":
            current = min(ready_jobs, key=lambda j: (j.priority, j.arrival, j.jid)).jid
        elif policy == "LOTTERY":
            pool: List[str] = []
            for j in ready_jobs:
                pool.extend([j.jid] * j.tickets)
            current = rng.choice(pool)
        elif policy == "RR":
            # If quantum expired, requeue the current job if unfinished.
            if current is not None and remaining[current] > 0 and rr_q >= quantum:
                rr_queue.append(current)
                current = None
                rr_q = 0
            if current is None:
                while rr_queue and remaining[rr_queue[0]] <= 0:
                    rr_queue.popleft()
                if rr_queue:
                    current = rr_queue.popleft()
                    rr_q = 0
                else:
                    # New arrival can be selected immediately.
                    pending = [j.jid for j in ready_jobs if j.jid != current]
                    current = sorted(pending)[0]

        if current is None:
            timeline.append((t, "IDLE"))
            t += 1
            continue

        if current not in first:
            first[current] = t
        if last_running is not None and current != last_running:
            context_switches += 1
        last_running = current
        timeline.append((t, current))
        remaining[current] -= 1
        if policy == "RR":
            rr_q += 1
        t += 1

        if remaining[current] == 0:
            completion[current] = t
            if policy == "RR":
                rr_q = 0
            current = None

    return _finalize(policy, jobs, first, completion, context_switches, timeline)


def simulate_multicore_rr(jobs: List[Job], cores: int = 2, quantum: int = 2,
                          queue_mode: str = "global", migration_penalty: int = 0) -> Dict[str, object]:
    """Lightweight multicore RR simulator.

    queue_mode='global': all ready jobs share one queue.
    queue_mode='per_core': arriving jobs are assigned round-robin to per-core queues and remain there.
    migration_penalty models a lost tick when a job executes on a different core from its previous core.
    This is deliberately a teaching model, not Linux.
    """
    if cores < 1 or quantum < 1 or migration_penalty < 0:
        raise ValueError("invalid cores/quantum/migration_penalty")
    queue_mode = queue_mode.lower()
    if queue_mode not in {"global", "per_core"}:
        raise ValueError("queue_mode must be global or per_core")
    jobs = sorted([Job(**asdict(j)) for j in jobs], key=lambda j: (j.arrival, j.jid))
    remaining = {j.jid: j.burst for j in jobs}
    first: Dict[str, int] = {}
    completion: Dict[str, int] = {}
    migrations = {j.jid: 0 for j in jobs}
    last_core: Dict[str, Optional[int]] = {j.jid: None for j in jobs}
    penalty_left = {j.jid: 0 for j in jobs}
    t = 0
    global_q: deque[str] = deque()
    per_q: List[deque[str]] = [deque() for _ in range(cores)]
    current: List[Optional[str]] = [None] * cores
    q_used: List[int] = [0] * cores
    admitted = set()
    assignment_counter = 0
    timeline: List[Dict[str, object]] = []
    context_switches = 0
    prev: List[Optional[str]] = [None] * cores

    def enqueue(jid: str, preferred_core: Optional[int] = None):
        nonlocal assignment_counter
        if queue_mode == "global":
            global_q.append(jid)
        else:
            if preferred_core is not None:
                c = preferred_core % cores
            else:
                c = assignment_counter % cores
                assignment_counter += 1
            per_q[c].append(jid)

    total_burst = sum(j.burst for j in jobs)
    latest_arrival = max((j.arrival for j in jobs), default=0)
    # Conservative deterministic guard. A valid run must make useful-service
    # progress; migration delay may add time but must never create a livelock.
    max_ticks = latest_arrival + total_burst + (
        total_burst + len(jobs) * cores
    ) * (migration_penalty + 1) + 100

    while len(completion) < len(jobs):
        if t > max_ticks:
            raise RuntimeError(
                "multicore RR exceeded deterministic progress bound; "
                "check migration/quantum accounting"
            )
        for j in jobs:
            if j.arrival <= t and j.jid not in admitted:
                admitted.add(j.jid)
                enqueue(j.jid, j.affinity)

        for c in range(cores):
            jid = current[c]
            if jid is not None and remaining[jid] > 0 and q_used[c] >= quantum:
                enqueue(jid, c if queue_mode == "per_core" else None)
                current[c] = None
                q_used[c] = 0
            if jid is not None and remaining[jid] <= 0:
                current[c] = None
                q_used[c] = 0

            if current[c] is None:
                q = global_q if queue_mode == "global" else per_q[c]
                while q and remaining[q[0]] <= 0:
                    q.popleft()
                if q:
                    current[c] = q.popleft()
                    q_used[c] = 0

        row = {"time": t}
        for c in range(cores):
            jid = current[c]
            row[f"core{c}"] = jid or "IDLE"
            if jid is None:
                prev[c] = None
                continue
            if jid not in first:
                first[jid] = t
            if prev[c] is not None and prev[c] != jid:
                context_switches += 1
            prev[c] = jid

            migrated = last_core[jid] is not None and last_core[jid] != c
            if migrated and penalty_left[jid] == 0:
                # A change of core is a migration even when its modeled cost is
                # zero. Accounting and cost are intentionally independent.
                migrations[jid] += 1
                penalty_left[jid] = migration_penalty
            last_core[jid] = c

            if penalty_left[jid] > 0:
                penalty_left[jid] -= 1
                # Migration delay is elapsed overhead, not useful RR service.
                # Charging it to q_used can livelock when quantum <= penalty.
                continue

            remaining[jid] -= 1
            q_used[c] += 1
            if remaining[jid] == 0:
                completion[jid] = t + 1
                current[c] = None
                q_used[c] = 0
        timeline.append(row)
        t += 1

    result = _finalize(f"MULTICORE_RR_{queue_mode.upper()}", jobs, first, completion,
                       context_switches, [], migrations)
    summary = result.summary()
    summary["cores"] = cores
    summary["queue_mode"] = queue_mode
    summary["migration_penalty"] = migration_penalty
    summary["migrations"] = sum(migrations.values())
    return {"summary": summary, "jobs": [asdict(j) for j in result.jobs], "timeline": timeline}



def simulate_custom_preemptive(jobs: List[Job], selector, policy_name: str = "CUSTOM") -> SimulationResult:
    """Extension hook for student research policies.

    selector signature: selector(t, ready_jobs, remaining, history) -> jid
    The selected ready job runs for one tick. This keeps the simulation engine fixed while
    allowing students to implement workload-aware/predictive selection logic in a small function.
    """
    jobs = sorted([Job(**asdict(j)) for j in jobs], key=lambda j: (j.arrival, j.jid))
    remaining = {j.jid: j.burst for j in jobs}
    first: Dict[str, int] = {}
    completion: Dict[str, int] = {}
    timeline: List[Tuple[int, str]] = []
    history: List[str] = []
    t = 0
    last = None
    switches = 0
    while len(completion) < len(jobs):
        ready = [j for j in jobs if j.arrival <= t and remaining[j.jid] > 0]
        if not ready:
            timeline.append((t, "IDLE")); history.append("IDLE"); t += 1; last = None; continue
        jid = selector(t, ready, dict(remaining), tuple(history))
        if jid not in {j.jid for j in ready}:
            raise ValueError(f"selector returned non-ready job {jid}")
        if jid not in first: first[jid] = t
        if last is not None and jid != last: switches += 1
        last = jid
        timeline.append((t, jid)); history.append(jid)
        remaining[jid] -= 1; t += 1
        if remaining[jid] == 0: completion[jid] = t
    return _finalize(policy_name, jobs, first, completion, switches, timeline)

def write_json(obj: object, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

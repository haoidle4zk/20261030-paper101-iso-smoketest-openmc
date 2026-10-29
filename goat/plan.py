"""Chia job cho các runner: Round-Robin trên mọi tài khoản ACTIVE x runners_per_account.

plan.json = { "<account>": { "1": ["<job_id>", ...], "2": [...] }, ... }
Job được sắp theo năng lượng giảm dần trước khi chia để tải nặng trải đều các runner.
"""

import json
from pathlib import Path


def build(rows: list, accounts: list, runners_per_account: int) -> dict:
    if not accounts:
        raise ValueError("Không có tài khoản ACTIVE nào")
    slots = [(a, str(r)) for r in range(1, runners_per_account + 1) for a in accounts]
    plan = {a: {str(r): [] for r in range(1, runners_per_account + 1)} for a in accounts}
    ordered = sorted(rows, key=lambda r: (-r["energy_kev"], -r["thickness_cm"], r["job_id"]))
    for i, r in enumerate(ordered):
        a, rid = slots[i % len(slots)]
        plan[a][rid].append(r["job_id"])
    return plan


def write(path: Path, plan: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1)


def read(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

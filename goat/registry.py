"""Sổ cái mọi project: registry/projects.json. Chỉ được ghi qua module này."""

import json
import time

from . import paths


def load() -> dict:
    if paths.REGISTRY.exists():
        with open(paths.REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"projects": {}}


def save(data: dict):
    paths.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.REGISTRY, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def update(name: str, **fields):
    data = load()
    rec = data["projects"].setdefault(name, {})
    rec.update(fields)
    rec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(data)


def running_projects() -> list:
    return [n for n, r in load()["projects"].items() if r.get("stage") == "RUNNING"]

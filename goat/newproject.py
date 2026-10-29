"""Tạo project mới từ template, đúng chuẩn tên và cấu trúc."""

import shutil
from datetime import date

from . import naming, paths, registry, ui
from .ui import GateError


def create(project: str, variant: str, tool: str, on: str = None) -> str:
    tool = {t.lower(): t for t in naming.TOOLS}.get(tool.lower())
    if not tool:
        raise GateError(f"Công cụ phải thuộc {naming.TOOLS}")
    d = date.fromisoformat(on.replace(".", "-")) if on else date.today()
    name = naming.make_project_name(project, variant, tool, d)
    dest = paths.project_dir(name)
    if dest.exists():
        raise GateError(f"Đã tồn tại: {dest}")

    tpl = paths.TEMPLATES / "project"
    shutil.copytree(tpl, dest)
    y = (dest / "project.yaml").read_text(encoding="utf-8")
    y = y.replace("__NAME__", name).replace("__TOOL__", tool)
    defaults = (paths.TEMPLATES / "tool_defaults" / f"{tool.lower()}.yaml").read_text(encoding="utf-8")
    y = y.replace("__RUN_BLOCK__", defaults.strip())
    (dest / "project.yaml").write_text(y, encoding="utf-8")
    for sub in ("sim/MAIN", "results", "logs", "theory"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    registry.update(name, stage="CREATED", tool=tool, repo=naming.repo_name(name))
    ui.ok(f"Đã tạo projects/{name}")
    return name

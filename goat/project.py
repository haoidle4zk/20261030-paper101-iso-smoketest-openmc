"""Đọc / kiểm tra project.yaml và quản lý vòng đời (stage) của project.

Vòng đời bắt buộc (chỉ đi tiến, mỗi bước có cổng kiểm tra):
  CREATED -> READY -> RUNNING -> COMPLETE -> HARVESTED -> ANALYZED -> VALIDATED -> DELIVERED -> ARCHIVED
"""

import json
import time
from pathlib import Path

import yaml

from . import paths, naming
from .ui import GateError

STAGES = ["CREATED", "READY", "RUNNING", "COMPLETE", "HARVESTED", "ANALYZED", "VALIDATED", "DELIVERED", "ARCHIVED"]

PARSERS = ["csv_last_row", "csv_all_rows", "fluka_usrtrack", "phits_track", "custom"]


class Project:
    def __init__(self, name: str):
        naming.check_project_name(name)
        self.name = name
        self.dir = paths.project_dir(name)
        if not self.dir.is_dir():
            raise GateError(f"Không tìm thấy project: {self.dir}")
        self.cfg = self._load_cfg()

    # ---------- đường dẫn ----------
    @property
    def sim(self) -> Path:
        return self.dir / paths.SIM_DIR

    @property
    def results(self) -> Path:
        return self.dir / paths.RESULTS_DIR

    @property
    def logs(self) -> Path:
        return self.dir / paths.LOGS_DIR

    @property
    def deploy_dir(self) -> Path:
        return self.dir / paths.DEPLOY_DIR

    @property
    def manifest_path(self) -> Path:
        return self.logs / paths.MANIFEST_FILE

    @property
    def plan_path(self) -> Path:
        return self.logs / paths.PLAN_FILE

    @property
    def drive_folders_path(self) -> Path:
        return self.logs / paths.DRIVE_FOLDERS_FILE

    @property
    def drive_only(self) -> bool:
        return self.cfg.get("storage", "local") == "drive_only"

    @property
    def tool(self) -> str:
        return self.cfg["tool"]

    @property
    def subprojects(self) -> list:
        return self.cfg["subprojects"]

    @property
    def repo(self) -> str:
        return self.cfg.get("github", {}).get("repo") or naming.repo_name(self.name)

    # ---------- cấu hình ----------
    def _load_cfg(self) -> dict:
        p = self.dir / paths.PROJECT_FILE
        if not p.exists():
            raise GateError(f"Thiếu {p}")
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def save_cfg(self):
        with open(self.dir / paths.PROJECT_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.cfg, f, allow_unicode=True, sort_keys=False)

    def validate_cfg(self) -> list:
        """Trả về danh sách lỗi cấu hình (rỗng = hợp lệ)."""
        c, errs = self.cfg, []
        if c.get("name") != self.name:
            errs.append(f"project.yaml: name phải đúng bằng tên thư mục '{self.name}'")
        tool_from_name = naming.check_project_name(self.name)["tool"]
        if c.get("tool") != tool_from_name:
            errs.append(f"project.yaml: tool phải là '{tool_from_name}' (khớp hậu tố tên project)")
        for key in ("primaries", "runners_per_account", "timeout_minutes"):
            if not isinstance(c.get(key), int) or c.get(key) <= 0:
                errs.append(f"project.yaml: {key} phải là số nguyên dương")
        if c.get("storage", "local") not in ("local", "drive_only"):
            errs.append("project.yaml: storage phải là local hoặc drive_only")
        if c.get("timeout_minutes", 0) > 355:
            errs.append("project.yaml: timeout_minutes tối đa 355 (GitHub giết job ở 360 phút)")
        run = c.get("run") or {}
        if not run.get("command"):
            errs.append("project.yaml: run.command chưa điền")
        if run.get("parser") not in PARSERS:
            errs.append(f"project.yaml: run.parser phải thuộc {PARSERS}")
        if run.get("parser") in ("csv_last_row", "csv_all_rows") and not run.get("result_file"):
            errs.append("project.yaml: run.result_file bắt buộc khi parser là csv_*")
        if run.get("parser") == "custom" and not (self.dir / "parsers" / "parser.py").exists():
            errs.append("parser custom cần file parsers/parser.py có hàm parse(workdir, job) -> list[dict]")
        subs = c.get("subprojects") or []
        if not subs:
            errs.append("project.yaml: cần ít nhất 1 subproject")
        seen = set()
        for i, s in enumerate(subs):
            tag = f"subprojects[{i}]"
            n = s.get("name", "")
            if not n or not naming.re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", n):
                errs.append(f"{tag}: name chỉ gồm chữ/số/gạch ngang")
            if n in seen:
                errs.append(f"{tag}: trùng tên subproject '{n}'")
            seen.add(n)
            for key in ("samples", "thicknesses_cm", "energies_kev"):
                if not s.get(key):
                    errs.append(f"{tag} ({n}): thiếu {key}")
            if not s.get("input_template"):
                errs.append(f"{tag} ({n}): thiếu input_template")
            if not (self.sim / n).is_dir():
                errs.append(f"{tag}: thiếu thư mục code/input sim/{n}/")
        return errs

    # ---------- trạng thái ----------
    def state(self) -> dict:
        p = self.logs / paths.STATE_FILE
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"stage": "CREATED", "history": []}

    @property
    def stage(self) -> str:
        return self.state()["stage"]

    def set_stage(self, stage: str, note: str = ""):
        assert stage in STAGES
        st = self.state()
        st["history"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "from": st["stage"], "to": stage, "note": note})
        st["stage"] = stage
        self.logs.mkdir(parents=True, exist_ok=True)
        with open(self.logs / paths.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2, ensure_ascii=False)
        from . import registry
        registry.update(self.name, stage=stage, tool=self.tool, repo=self.repo)

    def require_stage(self, *allowed: str):
        if self.stage not in allowed:
            raise GateError(
                f"Project đang ở stage {self.stage}; lệnh này chỉ chạy khi stage thuộc {list(allowed)}. "
                "Xem docs/runbooks/00_vong_doi.md để biết bước tiếp theo."
            )


def list_projects() -> list:
    if not paths.PROJECTS.is_dir():
        return []
    return sorted(p.name for p in paths.PROJECTS.iterdir() if p.is_dir() and not p.name.startswith("."))

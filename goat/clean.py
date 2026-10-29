"""CLEAN: phát hiện và cách ly file rác. Chỉ những gì nằm trong danh sách cho phép dưới đây mới được tồn tại.

- File rác KHÔNG bị xoá: được CHUYỂN vào tmp/_quarantine/<thời điểm>/<đường dẫn cũ> (lấy lại được).
- tmp/: mục cũ hơn 7 ngày bị xoá; tmp/_quarantine/: lô cũ hơn 30 ngày bị xoá.
- Project LEGACY không bị quét (chỉ đọc).
- Mọi lệnh đổi stage gọi require_clean(): còn rác => FAIL, phải chạy `goat clean` trước.
"""

import re
import shutil
import time
from pathlib import Path

from . import paths, registry, ui
from .ui import GateError

ROOT_ALLOWED = {
    "HUONG_DAN_CAI_DAT.txt", "VI_DU_PROMPT.txt", "GEMINI.md", "AGENTS.md", "README.md", "requirements.txt", "setup.ps1", "setup.sh", ".gitignore",
    "goat", "docs", "templates", "binaries", "secrets", "registry", "projects", ".agents",
    "_legacy_archive", ".git", ".venv", "tmp",
}
PROJECT_ALLOWED = {"project.yaml", "README.md", "sim", "parsers", "theory", "analysis", "results", "logs", ".deploy"}
LOG_FILES = {"manifest.csv", "plan.json", "state.json", "drive_folders.json", "status.json", "checklist.csv", "rescues.json"}
LOG_RE = re.compile(r"^deploy_\d{8}_\d{6}\.json$")
JUNK_NAME_RE = re.compile(r"(^~\$)|(\.(bak|tmp|orig|swp)$)|(~$)|(backup)|(\bcopy\b)|( - Copy)|(_old\b)|(^check_.*\.py$)|(^fix_.*\.py$)|(^debug_.*)", re.I)
SIM_RESULT_RE = re.compile(r"\.(root|h5)$|^process_results\.csv$|^fort\.\d+$|_fort\.\d+$", re.I)
TMP_DAYS, QUARANTINE_DAYS = 7, 30
TIERS = set(paths.TIERS)


def _legacy() -> set:
    return {n for n, r in registry.load()["projects"].items() if r.get("stage") == "LEGACY"}


def _is_junk_name(p: Path) -> bool:
    return p.name in ("__pycache__", ".pytest_cache", ".ipynb_checkpoints", "Thumbs.db", ".DS_Store") or bool(JUNK_NAME_RE.search(p.name))


def _scan_results(proj: Path, custom_analysis: bool) -> list:
    out = []
    res = proj / "results"
    for item in res.iterdir():
        if item.name not in TIERS:
            out.append((item, "results/ chỉ chứa 5 tầng chuẩn"))
    for f in res.rglob("*"):
        rel = f.relative_to(res).parts
        if f.is_dir():
            if _is_junk_name(f):
                out.append((f, "thư mục rác"))
            continue
        tier, rest = rel[0], rel[1:]
        name = f.name
        if _is_junk_name(f):
            out.append((f, "tên file rác/backup"))
        elif tier == "01_Summary_Excel_Results":
            ok = len(rest) == 2 and (
                re.fullmatch(rf"0[1-6]_(MAC|LAC|HVL|TVL|MFP|RPE)_{re.escape(rest[0])}\.xlsx", name)
                or name == f"Analysis_Long_{rest[0]}.csv"
                or (custom_analysis and f.suffix.lower() in (".xlsx", ".csv")))
            if not ok:
                out.append((f, "01_: chỉ chứa Excel/CSV do goat analyze tạo"))
        elif tier == "02_Flux_Data":
            ok = (len(rest) == 2 and name == f"Raw_Merged_{rest[0]}.csv") or (len(rest) == 3 and f.suffix == ".csv")
            if not ok:
                out.append((f, "02_: chỉ chứa <sub>/<SAMPLE>/<job>.csv và Raw_Merged_<sub>.csv"))
        elif tier == "05_Execution_Logs_and_Benchmarks":
            ok = (len(rest) == 2 and rest[0] == "_runners" and f.suffix == ".json") or \
                 (len(rest) == 2 and name == f"benchmark_{rest[0]}.csv") or \
                 (len(rest) == 1 and name == "Benchmark_Report.xlsx") or                  (len(rest) == 2 and rest[0] != "_runners" and name.endswith(".log"))
            if not ok:
                out.append((f, "05_: chỉ chứa Benchmark_Report.xlsx, <sub>/benchmark_<sub>.csv, <sub>/<job>.log, _runners/*.json"))
        elif tier == "03_Simulation_Outputs":
            if len(rest) < 3:
                out.append((f, "03_: chỉ chứa <sub>/<job>/<file output>"))
        elif tier == "04_Input_Decks":
            if len(rest) != 2:
                out.append((f, "04_: chỉ chứa file input trong <sub>/ (Input_Decks_<sub>.zip hoặc input tải về)"))
    return out


def scan_project(proj: Path) -> list:
    """Trả về [(đường dẫn, lý do)] các mục rác trong một project v2."""
    out = []
    custom = False
    yml = proj / "project.yaml"
    if yml.exists():
        try:
            import yaml
            custom = (yaml.safe_load(yml.read_text(encoding="utf-8")) or {}).get("analysis", {}).get("type") == "custom"
        except Exception:  # noqa: BLE001
            pass
    for item in proj.iterdir():
        if item.name not in PROJECT_ALLOWED:
            out.append((item, "không thuộc cấu trúc project"))
    for d in ("sim", "parsers", "analysis", "theory"):
        base = proj / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if _is_junk_name(f):
                out.append((f, "tên file/thư mục rác"))
            elif d == "sim" and f.is_dir() and f.name in ("build", "_work", "out", ".vs"):
                out.append((f, "sim/ không chứa thư mục build/output (runner tự build)"))
            elif d == "sim" and f.is_file() and SIM_RESULT_RE.search(f.name):
                out.append((f, "sim/ không chứa file kết quả mô phỏng"))
            elif d == "parsers" and f.is_file() and f.name not in ("parser.py", "README.md"):
                out.append((f, "parsers/ chỉ chứa parser.py"))
            elif d == "analysis" and f.is_file() and f.suffix != ".py" and f.name != "README.md":
                out.append((f, "analysis/ chỉ chứa script .py"))
            elif d == "theory" and f.is_file() and f.suffix.lower() not in (".csv", ".xlsx", ".md", ".txt"):
                out.append((f, "theory/ chỉ chứa CSV/Excel/ghi chú"))
    logs = proj / "logs"
    if logs.is_dir():
        for f in logs.iterdir():
            if not (f.is_file() and (f.name in LOG_FILES or LOG_RE.match(f.name))):
                out.append((f, "logs/ chỉ chứa file do goat ghi"))
    dep = proj / ".deploy"
    if dep.is_dir():
        for f in dep.iterdir():
            if f.name != "bundle":
                out.append((f, ".deploy/ chỉ chứa bundle/"))
    if (proj / "results").is_dir():
        out += _scan_results(proj, custom)
    # bỏ mục con khi mục cha đã bị đánh dấu
    marked = sorted(out, key=lambda x: len(x[0].parts))
    keep = []
    for p, why in marked:
        if not any(p != q and q in p.parents for q, _ in keep):
            keep.append((p, why))
    return keep


def scan_root() -> list:
    out = [(i, "không thuộc cấu trúc gốc") for i in paths.ROOT.iterdir() if i.name not in ROOT_ALLOWED]
    from . import naming
    legacy = _legacy()
    if paths.PROJECTS.is_dir():
        for d in paths.PROJECTS.iterdir():
            if d.name in legacy:
                continue
            if not d.is_dir():
                out.append((d, "projects/ chỉ chứa thư mục project"))
                continue
            try:
                naming.check_project_name(d.name)
            except ValueError:
                out.append((d, "tên project sai chuẩn (tạo bằng goat new)"))
                continue
            out += scan_project(d)
    return out


def require_clean(project_dir: Path = None):
    issues = scan_root() if project_dir is None else [i for i in scan_root() if project_dir in i[0].parents or i[0] == project_dir
                                                     or i[0].parent == paths.ROOT]
    if issues:
        for p, why in issues[:15]:
            ui.fail(f"rác: {p.relative_to(paths.ROOT)}  ({why})")
        raise GateError(f"Có {len(issues)} mục rác. Chạy `python -m goat clean` (cách ly vào tmp/_quarantine) rồi chạy lại lệnh.")


def _purge_tmp():
    tmp = paths.ROOT / "tmp"
    if not tmp.is_dir():
        return 0
    now, n = time.time(), 0
    for item in tmp.iterdir():
        if item.name == "_quarantine":
            for batch in item.iterdir():
                if now - batch.stat().st_mtime > QUARANTINE_DAYS * 86400:
                    shutil.rmtree(batch, ignore_errors=True) if batch.is_dir() else batch.unlink()
                    n += 1
        elif now - item.stat().st_mtime > TMP_DAYS * 86400:
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink()
            n += 1
    return n


def clean(dry_run: bool = False) -> int:
    issues = scan_root()
    if not issues:
        ui.ok("không có rác")
    batch = paths.ROOT / "tmp" / "_quarantine" / time.strftime("%Y%m%d_%H%M%S")
    moved, failed = 0, 0
    for p, why in issues:
        rel = p.relative_to(paths.ROOT)
        if dry_run:
            ui.warn(f"sẽ cách ly: {rel}  ({why})")
            continue
        dest = batch / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(p), str(dest))
            ui.ok(f"cách ly: {rel}  ({why})")
            moved += 1
        except OSError as e:
            ui.fail(f"không chuyển được {rel}: {e} (file đang mở? đóng chương trình rồi chạy lại)")
            failed += 1
    if moved:
        ui.info(f"Đã chuyển {moved} mục vào {batch.relative_to(paths.ROOT)} (lấy lại được trong {QUARANTINE_DAYS} ngày)")
    if not dry_run:
        n = _purge_tmp()
        if n:
            ui.ok(f"đã xoá {n} mục quá hạn trong tmp/")
    if failed:
        raise GateError(f"{failed} mục không cách ly được")
    return moved

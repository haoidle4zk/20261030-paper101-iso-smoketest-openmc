"""Đọc kết quả thô của công cụ mô phỏng sau mỗi job -> list[dict] (các dòng ghi vào <job>.csv).

LUẬT: parser KHÔNG BAO GIỜ trả giá trị giả (0.0, 1e-8, số lý thuyết...) khi đọc lỗi.
Đọc lỗi => ném exception => job bị coi là FAILED, không upload CSV, sẽ được cứu hộ chạy lại.
Giá trị 0 thật sự do công cụ ghi ra (ví dụ mẫu cản 100%) thì giữ nguyên 0.
"""

import csv
import importlib.util
import re
from pathlib import Path


class ParseError(Exception):
    pass


def _one(workdir: Path, pattern: str) -> Path:
    found = sorted(workdir.glob(pattern))
    found = [f for f in found if f.is_file() and f.stat().st_size > 0]
    if not found:
        raise ParseError(f"không thấy file kết quả khớp '{pattern}'")
    return found[0]


def csv_last_row(workdir: Path, job: dict, opts: dict, result_file: str) -> list:
    p = _one(workdir, result_file)
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ParseError(f"{p.name} rỗng")
    return [rows[-1]]


def csv_all_rows(workdir: Path, job: dict, opts: dict, result_file: str) -> list:
    p = _one(workdir, result_file)
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ParseError(f"{p.name} rỗng")
    return rows


def _usrtrack(path: Path) -> tuple:
    """USRTRACK ASCII (fort.21/22) -> (fluence tích phân, sai số % bin đầu)."""
    bin_width, values, errors, mode = None, [], [], None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.search(r"\(\s*([\d.+\-Ee]+)\s*GeV wide\)", line)
        if m:
            bin_width = float(m.group(1))
        if "Data follow in a vector A(ie)" in line:
            mode = "v"
            continue
        if "Percentage errors follow in a vector" in line:
            mode = "e"
            continue
        if mode:
            for tok in line.split():
                try:
                    (values if mode == "v" else errors).append(float(tok))
                except ValueError:
                    pass
    if not values:
        raise ParseError(f"{path.name}: không có khối dữ liệu USRTRACK")
    fluence = sum(v * bin_width for v in values) if bin_width else sum(values)
    return fluence, (errors[0] if errors else None)


def fluka_usrtrack(workdir: Path, job: dict, opts: dict, result_file: str) -> list:
    files = opts.get("files") or {"peak": "*fort.21", "total": "*fort.22"}
    row = {}
    for key, pattern in files.items():
        val, err = _usrtrack(_one(workdir, pattern.format(**job)))
        row[f"{key}_value"] = val
        row[f"{key}_err_pct"] = err if err is not None else ""
    return [row]


def _phits_track(path: Path, region: int) -> tuple:
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        p = line.split()
        if len(p) >= 5 and p[0] == "1" and p[1] == str(region):
            return float(p[-2]), float(p[-1])
    raise ParseError(f"{path.name}: không thấy dòng region {region}")


def phits_track(workdir: Path, job: dict, opts: dict, result_file: str) -> list:
    files = opts.get("files") or {"peak": "peak_{job}.out", "total": "track_{job}.out"}
    region = int(opts.get("region", 104))
    row = {}
    for key, pattern in files.items():
        val, err = _phits_track(_one(workdir, pattern.format(**job)), region)
        row[f"{key}_value"] = val
        row[f"{key}_rel_err"] = err
    return [row]


def custom(workdir: Path, job: dict, opts: dict, result_file: str, project_root: Path = None) -> list:
    path = Path(project_root or ".") / "parsers" / "parser.py"
    spec = importlib.util.spec_from_file_location("goat_custom_parser", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = mod.parse(workdir, job)
    if not rows:
        raise ParseError("custom parser trả về rỗng")
    return rows


def run_parser(name: str, workdir: Path, job: dict, run_cfg: dict, project_root: Path) -> list:
    opts = run_cfg.get("parser_options") or {}
    rf = run_cfg.get("result_file", "")
    if name == "custom":
        return custom(workdir, job, opts, rf, project_root)
    fn = {"csv_last_row": csv_last_row, "csv_all_rows": csv_all_rows,
          "fluka_usrtrack": fluka_usrtrack, "phits_track": phits_track}[name]
    return fn(workdir, job, opts, rf)

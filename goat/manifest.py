"""Sinh manifest.csv: danh sách ĐẦY ĐỦ mọi job phải chạy. Đây là hợp đồng duy nhất để
đếm tiến độ, phát hiện job thiếu, cứu hộ và kiểm định. Không ai được sửa tay manifest.csv.

Mỗi subproject khai báo lưới (samples x thicknesses_cm x energies_kev), hoặc
jobs_file: một CSV trong sim/<sub>/ với cột sample,thickness_cm,energy_kev (lưới không đều).
Có thể thêm blank: {sample: BLANK, thickness_cm: 0} để chạy thêm job không mẫu cho mỗi năng lượng.

input_template là tên file input bên trong sim/<sub>/, dùng các biến:
  {sample} {sample_lower} {thickness} {energy} {job}
  ({thickness}/{energy} viết theo chuẩn chữ p: 0p5, 59p5)
"""

import csv
from pathlib import Path

from . import naming

COLUMNS = ["job_id", "subproject", "job", "sample", "thickness_cm", "energy_kev", "input_file"]


def _rows_for_sub(sim_dir: Path, sub: dict) -> list:
    combos = []
    if sub.get("jobs_file"):
        with open(sim_dir / sub["name"] / sub["jobs_file"], "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                combos.append((r["sample"], float(r["thickness_cm"]), float(r["energy_kev"])))
    else:
        for s in sub["samples"]:
            for t in sub["thicknesses_cm"]:
                for e in sub["energies_kev"]:
                    combos.append((s, float(t), float(e)))
    blank = sub.get("blank")
    if blank:
        for e in sub["energies_kev"]:
            combos.append((blank["sample"], float(blank["thickness_cm"]), float(e)))

    rows = []
    for s, t, e in combos:
        job = naming.job_name(s, t, e)
        inp = sub["input_template"].format(
            sample=s.upper(), sample_lower=s.lower(), thickness=naming.fmt_num(t), energy=naming.fmt_num(e), job=job
        )
        rows.append({
            "job_id": f"{sub['name']}/{job}",
            "subproject": sub["name"],
            "job": job,
            "sample": s.upper(),
            "thickness_cm": t,
            "energy_kev": e,
            "input_file": inp,
        })
    return rows


def build(project) -> tuple:
    """Trả về (rows, errors)."""
    rows, errs = [], []
    for sub in project.subprojects:
        try:
            rows.extend(_rows_for_sub(project.sim, sub))
        except (KeyError, FileNotFoundError, ValueError) as e:
            errs.append(f"subproject {sub.get('name')}: {e}")
    ids = [r["job_id"] for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errs.append(f"Trùng job_id ({len(dup)}): {dup[:5]}")
    missing_inputs = [r for r in rows if not (project.sim / r["subproject"] / r["input_file"]).exists()]
    if missing_inputs:
        errs.append(
            f"{len(missing_inputs)} file input không tồn tại, ví dụ: "
            + ", ".join(f"sim/{r['subproject']}/{r['input_file']}" for r in missing_inputs[:5])
        )
    return rows, errs


def write(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def read(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["thickness_cm"] = float(r["thickness_cm"])
        r["energy_kev"] = float(r["energy_kev"])
    return rows

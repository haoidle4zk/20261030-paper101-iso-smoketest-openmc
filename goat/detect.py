"""DETECT: nhận diện thư mục code/input của người dùng, rồi (tuỳ chọn) nạp vào project theo đúng chuẩn.

  python -m goat detect <thư mục>                       # chỉ báo cáo: công cụ, số input, mẫu/bề dày/năng lượng
  python -m goat detect <thư mục> --into <project> [--sub MAC] [--thickness-unit mm|cm]

--into (project phải ở stage CREATED, đúng công cụ):
  - chép code (Geant4: CMakeLists, src, include, macro phụ…) vào sim/<sub>/
  - chép từng file input vào sim/<sub>/ với TÊN CHUẨN <job>.<đuôi> (nội dung vật lý giữ nguyên)
  - nếu mọi input cùng một số hạt: thay số đó bằng @PRIMARIES@ và ghi primaries vào project.yaml
  - ghi sim/<sub>/jobs.csv và khối subprojects/run trong project.yaml
Mẫu/bề dày/năng lượng đọc từ NỘI DUNG input (đáng tin hơn) rồi mới tới TÊN FILE.
Input nào không xác định được đủ 3 giá trị => liệt kê ra và DỪNG (hỏi người dùng), không đoán bừa.
"""

import csv
import re
import shutil
from collections import Counter
from pathlib import Path

import yaml

from . import naming, paths, ui
from .ui import GateError

NUM = r"(\d+(?:[.p]\d+)?)"
SKIP_DIRS = {"build", "_work", "__pycache__", ".git", ".vs", "out", "results", "03_Simulation_Outputs",
             "01_Summary_Excel_Results", "02_Flux_Data", "05_Execution_Logs_and_Benchmarks"}
G4_SKIP_SUFFIX = {".csv", ".root", ".xlsx", ".zip", ".log", ".py", ".json", ".txt~", ".bak", ".pdf", ".png"}
G4_AUX_MACROS = re.compile(r"^(vis|init|init_vis|gui|run_all.*|batch|test.*)\.mac$", re.I)


def _num(s: str) -> float:
    return float(s.replace("p", "."))


def _files(root: Path):
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root).parts
        if f.is_file() and not (set(rel[:-1]) & SKIP_DIRS) and not any(x.startswith(".") for x in rel):
            yield f


# --------------------------------------------------------------------------- nhận diện công cụ
def guess_tool(root: Path) -> tuple:
    files = list(_files(root))
    if any(f.name == "CMakeLists.txt" for f in files) and any(f.suffix in (".cc", ".cpp") for f in files):
        return "Geant4", [f for f in files if f.suffix == ".mac" and not G4_AUX_MACROS.match(f.name)]
    inps = [f for f in files if f.suffix.lower() == ".inp"]
    fl = [f for f in inps if re.search(r"^\s*(GEOBEGIN|BEAM\b)", f.read_text(errors="ignore"), re.M)]
    ph = [f for f in inps if re.search(r"\[\s*(parameters|source|t-track|cell)\s*\]", f.read_text(errors="ignore"), re.I)]
    if len(fl) >= len(ph) and fl:
        return "FLUKA", fl
    if ph:
        return "PHITS", ph
    pys = [f for f in files if f.suffix == ".py" and f.name != "model.py"
           and re.search(r"^\s*(import openmc|from model import)", f.read_text(errors="ignore"), re.M)]
    if pys:
        return "OpenMC", pys
    return None, []


# --------------------------------------------------------------------------- đọc tham số 1 input
def _from_name(stem: str, g4_unit_mm: bool) -> dict:
    out = {}
    m = re.search(r"(?:^|[_\-.])(blank|s\d+[a-z]?)(?=$|[_\-.])", stem, re.I)
    if m:
        out["sample"] = m.group(1).upper()
    for pat, conv in ((rf"th{NUM}\s*cm", 1.0), (rf"(?:^|_){NUM}cm", 1.0), (rf"{NUM}\s*mm", 0.1),
                      (rf"(?:^|_)D{NUM}(?=_|$)", 0.1 if g4_unit_mm else 1.0)):
        m = re.search(pat, stem, re.I)
        if m:
            out["thickness_cm"] = _num(m.group(1)) * conv
            break
    for pat, conv in ((rf"{NUM}\s*kev", 1.0), (rf"{NUM}\s*mev", 1000.0), (rf"(?:^|_)E{NUM}(?=_|$)", 1.0)):
        m = re.search(pat, stem, re.I)
        if m:
            out["energy_kev"] = _num(m.group(1)) * conv
            break
    return out


UNIT = {"ev": 1e-3, "kev": 1.0, "mev": 1e3, "gev": 1e6, "mm": 0.1, "cm": 1.0, "um": 1e-4, "m": 100.0}


def _from_content(tool: str, text: str, g4_unit_mm: bool) -> tuple:
    """-> (tham số đọc được, (regex dòng chứa số hạt, số hạt) hoặc None)"""
    out, prim = {}, None
    if tool == "Geant4":
        m = re.search(r"^\s*/gun/energy\s+([\d.eE+-]+)\s*(\w+)", text, re.M | re.I)
        if m:
            out["energy_kev"] = float(m.group(1)) * UNIT.get(m.group(2).lower(), 1.0)
        m = re.search(r"^\s*/\S*material\S*\s+(\S+)", text, re.M | re.I)
        if m and re.fullmatch(r"(?i)blank|s\d+[a-z]?", m.group(1)):
            out["sample"] = m.group(1).upper()
        m = re.search(r"^\s*/\S*thick\S*\s+([\d.eE+-]+)\s*(mm|cm|um|m)?\s*$", text, re.M | re.I)
        if m:
            unit = (m.group(2) or ("mm" if g4_unit_mm else "cm")).lower()
            out["thickness_cm"] = float(m.group(1)) * UNIT[unit]
        m = re.search(r"^(\s*/run/beamOn\s+)(\d+)", text, re.M)
        if m:
            prim = (r"(?m)^(\s*/run/beamOn\s+)\d+", int(m.group(2)))
    elif tool == "PHITS":
        m = re.search(r"^\s*e0\s*=\s*([\d.eE+-]+)", text, re.M | re.I)
        if m:
            out["energy_kev"] = float(m.group(1)) * 1000.0
        m = re.search(r"^(\s*maxcas\s*=\s*)(\d+)", text, re.M | re.I)
        if m:
            prim = (r"(?mi)^(\s*maxcas\s*=\s*)\d+", int(m.group(2)))
    elif tool == "FLUKA":
        m = re.search(r"^BEAM\s+(-?[\d.eE+-]+)", text, re.M)
        if m:
            out["energy_kev"] = abs(float(m.group(1))) * 1e6
        m = re.search(r"^(START\s+)([\d.eE+]+)", text, re.M)
        if m:
            prim = (r"(?m)^(START\s+)[\d.eE+]+", int(float(m.group(2))))
    elif tool == "OpenMC":
        for key, rx in (("sample", r"sample\s*=\s*['\"](\w+)['\"]"), ("thickness_cm", r"thickness_cm\s*=\s*([\d.eE+-]+)"),
                        ("energy_kev", r"energy_kev\s*=\s*([\d.eE+-]+)")):
            m = re.search(rx, text)
            if m:
                out[key] = m.group(1).upper() if key == "sample" else float(m.group(1))
        m = re.search(r"(primaries\s*=\s*)(\d+)", text)
        if m:
            prim = (r"(primaries\s*=\s*)\d+", int(m.group(2)))
    return out, prim


def scan(src: Path, tool: str, inputs: list, g4_unit_mm: bool) -> tuple:
    rows, bad = [], []
    for f in inputs:
        text = f.read_text(encoding="utf-8", errors="replace")
        got = _from_name(f.stem, g4_unit_mm)
        cont, prim = _from_content(tool, text, g4_unit_mm)
        got.update({k: v for k, v in cont.items() if v is not None})
        if got.get("sample") == "BLANK":
            got.setdefault("thickness_cm", 0.0)
        miss = [k for k in ("sample", "thickness_cm", "energy_kev") if k not in got]
        if miss:
            bad.append((f, miss))
            continue
        got["job"] = naming.job_name(got["sample"], got["thickness_cm"], got["energy_kev"])
        rows.append({**got, "src": f, "prim": prim})
    return rows, bad


# --------------------------------------------------------------------------- cấu hình chạy theo công cụ
def run_overrides(tool: str, src: Path, rows: list) -> tuple:
    """-> (dict cập nhật khối run, danh sách cảnh báo)"""
    run, warns = {}, []
    if tool == "Geant4":
        cm = (src / "CMakeLists.txt").read_text(errors="ignore") if (src / "CMakeLists.txt").exists() else ""
        exes = re.findall(r"add_executable\s*\(\s*(\w+)", cm)
        exe = next((e for e in exes if e.lower() not in ("automac",)), exes[0] if exes else "Sim")
        run["command"] = "{sim_dir}/build/" + exe + " {input}"
        csvs = Counter(re.findall(r'"([\w.\-]+\.csv)"', " ".join(f.read_text(errors="ignore") for f in _files(src)
                                                               if f.suffix in (".cc", ".cpp", ".hh"))))
        if csvs:
            run["result_file"] = csvs.most_common(1)[0][0]
        else:
            warns.append("Không thấy tên file .csv kết quả trong code Geant4 — hỏi người dùng run.result_file")
        # chỉ chép macro phụ mà input thật sự gọi (/control/execute ...)
        called = set()
        for r in rows:
            called |= {Path(x).name for x in re.findall(r"^\s*/control/execute\s+(\S+)", r["src"].read_text(errors="ignore"), re.M)}
        run["copy_files"] = sorted(called)
    elif tool == "PHITS":
        text = rows[0]["src"].read_text(errors="ignore")
        stem = rows[0]["src"].stem
        tallies = re.findall(r"\[\s*t-track\s*\](.*?)(?=\n\s*\[|\Z)", text, re.I | re.S)
        files, region = {}, None
        for i, t in enumerate(tallies, 1):
            fm = re.search(r"^\s*file\s*=\s*(\S+)", t, re.M | re.I)
            rm = re.search(r"^\s*reg\s*=\s*(\d+)", t, re.M | re.I)
            if fm:
                pat = fm.group(1).replace(stem, "*")
                key = re.sub(r"[^a-z]", "", Path(fm.group(1)).stem.split("_")[0].lower()) or f"t{i}"
                files[key if key not in files else f"{key}{i}"] = pat
            if rm and region is None:
                region = int(rm.group(1))
        if files:
            run["parser_options"] = {"files": files, "region": region or 104}
        else:
            warns.append("Không thấy [T-Track] file= trong input PHITS — cần parser custom")
    elif tool == "FLUKA":
        text = rows[0]["src"].read_text(errors="ignore")
        files = {}
        for line in text.splitlines():
            f = line.split()
            # dòng định nghĩa: USRTRACK <loại> <hạt> <unit> <vùng> <thể tích> <bin><TÊN>; bỏ dòng tiếp nối (&)
            if len(f) >= 5 and f[0] == "USRTRACK" and re.fullmatch(r"[A-Za-z][\w-]*", f[2]) and \
                    re.fullmatch(r"-?\d+(\.0*)?", f[3]):
                unit = abs(int(float(f[3])))
                name = re.sub(r"^[\d.]+", "", f[-1]).lower() if len(f) >= 7 else ""
                files[re.sub(r"[^a-z0-9]", "", name) or f"u{unit}"] = f"*fort.{unit}"
        if files:
            run["parser_options"] = {"files": files}
        else:
            warns.append("Không thấy thẻ USRTRACK — cần parser custom (parsers/parser.py)")
    return run, warns


# --------------------------------------------------------------------------- lệnh
def detect(src: str, into: str = None, sub: str = "MAIN", thickness_unit: str = None):
    src = Path(src).resolve()
    if not src.is_dir():
        raise GateError(f"Không thấy thư mục {src}")
    tool, inputs = guess_tool(src)
    ui.step(f"Nhận diện {src}")
    if not tool:
        raise GateError("Không nhận ra công cụ (cần: CMakeLists+.cc+.mac / .inp FLUKA / .inp PHITS / script openmc)")
    g4_mm = (thickness_unit or "mm") == "mm"
    rows, bad = scan(src, tool, inputs, g4_mm)
    ui.ok(f"Công cụ: {tool} | {len(inputs)} file input | đọc được {len(rows)} | không đọc được {len(bad)}")
    if tool == "Geant4" and not thickness_unit:
        ui.warn("Geant4: bề dày trong macro không ghi đơn vị được hiểu là mm (đơn vị gốc của Geant4). "
                "Sai thì chạy lại với --thickness-unit cm.")
    if rows:
        for key in ("sample", "thickness_cm", "energy_kev"):
            vals = sorted({r[key] for r in rows}, key=lambda v: (isinstance(v, str), v))
            ui.info(f"{key:<13}: {len(vals)} giá trị: {vals[:25]}{' …' if len(vals) > 25 else ''}")
        prims = Counter(r["prim"][1] for r in rows if r["prim"])
        ui.info(f"số hạt trong input: {dict(prims) or 'không thấy'}")
        dup = [j for j, c in Counter(r["job"] for r in rows).items() if c > 1]
        for d in dup:
            srcs = [str(r["src"].relative_to(src)) for r in rows if r["job"] == d]
            bad.append((None, f"cùng job {d} ở {len(srcs)} file: {srcs[:3]}"))
        if dup:
            ui.warn("Có job trùng: thường do 2 thư mục input giống nhau (vd macros/ và macro2/). "
                    "Chỉ đưa MỘT thư mục input vào detect, hoặc hỏi người dùng dùng thư mục nào.")
    for f, miss in bad[:15]:
        ui.fail(f"{f.relative_to(src)}: thiếu {miss}" if f else miss)
    if bad:
        raise GateError(f"{len(bad)} input chưa xác định được mẫu/bề dày/năng lượng (hoặc trùng). "
                        "Hỏi người dùng quy ước tên file / nội dung — KHÔNG đoán.")
    run, warns = run_overrides(tool, src, rows)
    for w in warns:
        ui.warn(w)
    if not into:
        ui.info("Chỉ báo cáo. Nạp vào project: thêm --into <tên project> [--sub <tên nhóm>]")
        return

    from . import project
    p = project.Project(into)
    p.require_stage("CREATED")
    if p.tool != tool:
        raise GateError(f"Project là {p.tool} nhưng thư mục là {tool}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", sub):
        raise GateError("--sub chỉ gồm chữ/số/gạch ngang")
    dest = p.sim / sub
    if dest.exists() and any(dest.iterdir()):
        raise GateError(f"sim/{sub}/ đã có dữ liệu — chọn --sub khác hoặc người dùng tự dọn")
    dest.mkdir(parents=True, exist_ok=True)

    input_set = {r["src"].resolve() for r in rows}
    if tool == "Geant4":  # chép code, giữ cấu trúc
        for f in _files(src):
            aux_unused = f.suffix == ".mac" and G4_AUX_MACROS.match(f.name) and f.name not in run.get("copy_files", [])
            if f.resolve() not in input_set and f.suffix.lower() not in G4_SKIP_SUFFIX and not aux_unused:
                (dest / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest / f.relative_to(src))
    elif tool == "OpenMC" and (src / "model.py").exists():
        shutil.copy2(src / "model.py", dest / "model.py")

    prims = {r["prim"][1] for r in rows if r["prim"]}
    use_placeholder = len(prims) == 1 and all(r["prim"] for r in rows)
    ext = rows[0]["src"].suffix
    for r in rows:
        text = r["src"].read_text(encoding="utf-8", errors="replace")
        if use_placeholder:
            # FLUKA START cần số thực có dấu chấm (1000000.), nếu không FLUKA 4 dừng "not a valid fortran real"
            text = re.sub(r["prim"][0], r"\g<1>@PRIMARIES@" + ("." if tool == "FLUKA" else ""), text, count=1)
        if tool == "PHITS":  # tên file tally theo tên job mới
            text = text.replace(r["src"].stem, r["job"])
        (dest / f"{r['job']}{ext}").write_text(text, encoding="utf-8")
    with open(dest / "jobs.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample", "thickness_cm", "energy_kev"])
        for r in sorted(rows, key=lambda x: x["job"]):
            w.writerow([r["sample"], r["thickness_cm"], r["energy_kev"]])

    if tool == "PHITS" and "parser_options" in run:  # sau khi đổi tên, mẫu file tally theo {job}
        run["parser_options"]["files"] = {k: v.replace("*", "{job}") for k, v in run["parser_options"]["files"].items()}
    subs = [s for s in (p.cfg.get("subprojects") or []) if s.get("name") not in ("MAIN", sub)]
    subs.append({"name": sub,
                 "samples": sorted({r["sample"] for r in rows}),
                 "thicknesses_cm": sorted({r["thickness_cm"] for r in rows}),
                 "energies_kev": sorted({r["energy_kev"] for r in rows}),
                 "jobs_file": "jobs.csv", "input_template": "{job}" + ext})
    p.cfg["subprojects"] = subs
    p.cfg.setdefault("run", {}).update(run)
    if use_placeholder:
        p.cfg["primaries"] = prims.pop()
    p.save_cfg()
    if (p.sim / "MAIN").is_dir() and not any((p.sim / "MAIN").iterdir()):
        (p.sim / "MAIN").rmdir()
    ui.ok(f"Đã nạp {len(rows)} input vào sim/{sub}/ (tên chuẩn {{job}}{ext}), jobs.csv, project.yaml")
    ui.info(f"primaries = {p.cfg['primaries']}" + (" (đã thay bằng @PRIMARIES@ trong input)" if use_placeholder else
                                                   " — input ghi cứng số hạt khác nhau/không thấy: KIỂM TRA LẠI"))
    ui.info("Bước tiếp: xem lại project.yaml (run, storage, analysis) rồi: python -m goat check " + p.name)

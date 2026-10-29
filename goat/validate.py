"""VALIDATE: cổng chất lượng trước khi bàn giao. Mọi kiểm tra phải đạt, không có ngoại lệ.

  V1  Mỗi subproject có Raw_Merged với số dòng job đúng bằng manifest (không thiếu, không thừa, không trùng).
  V2  Mọi file .xlsx trong results/01_Summary_Excel_Results: 0 ô trống / NaN / inf / chuỗi lỗi Excel (#DIV/0!...).
  V3  (attenuation) Giá trị Sim trong Excel được TÍNH LẠI từ Raw_Merged và phải khớp tuyệt đối
      -> chặn mọi số liệu đắp tay / sửa tay.
  V4  Không còn file tạm/rác trong results/ (_work, .tmp, ~$...).
"""

import math

import pandas as pd

from . import analysis, manifest, ui
from .ui import GateError

EXCEL_ERRORS = ("#DIV/0!", "#N/A", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#NULL!")


def _v1(p) -> list:
    errs = []
    rows = manifest.read(p.manifest_path)
    for sub in p.subprojects:
        n = sub["name"]
        f = p.results / "02_Flux_Data" / n / f"Raw_Merged_{n}.csv"
        if not f.exists():
            errs.append(f"V1 thiếu {f.name}")
            continue
        raw = pd.read_csv(f)
        want = {r["job_id"] for r in rows if r["subproject"] == n}
        have = set(raw["job_id"])
        if want - have:
            errs.append(f"V1 {n}: thiếu {len(want - have)} job trong Raw_Merged")
        if have - want:
            errs.append(f"V1 {n}: {len(have - want)} job lạ không có trong manifest")
    return errs


def _v2(p) -> list:
    errs = []
    if ((p.cfg.get("analysis") or {}).get("type") or "none") == "none":
        ui.info("V2 bỏ qua (analysis.type = none: không có Excel)")
        return []
    files = sorted((p.results / "01_Summary_Excel_Results").rglob("*.xlsx"))
    if not files:
        return ["V2 không có file Excel nào trong results/01_Summary_Excel_Results"]
    for f in files:
        if f.name.startswith("~$"):
            continue
        for sheet, df in pd.read_excel(f, sheet_name=None).items():
            n_null = int(df.isnull().sum().sum())
            num = df.select_dtypes("number")
            n_inf = int(num.map(lambda v: isinstance(v, float) and math.isinf(v)).sum().sum()) if not num.empty else 0
            n_err = int(df.astype(str).isin(EXCEL_ERRORS).sum().sum())
            if n_null or n_inf or n_err:
                errs.append(f"V2 {f.name} [{sheet}]: rỗng/NaN={n_null} inf={n_inf} lỗi Excel={n_err}")
    ui.info(f"V2 đã quét {len(files)} file Excel")
    return errs


def _v3(p) -> list:
    if (p.cfg.get("analysis") or {}).get("type") != "attenuation":
        ui.info("V3 bỏ qua (analysis custom: script custom tự chịu trách nhiệm, ghi rõ trong báo cáo)")
        return []
    errs = []
    for sub in p.subprojects:
        n = sub["name"]
        fresh = analysis.compute_attenuation(p, sub).reset_index(drop=True)
        for i, q in enumerate(analysis.QUANTITIES, 1):
            f = p.results / "01_Summary_Excel_Results" / n / f"{i:02d}_{q}_{n}.xlsx"
            if not f.exists():
                errs.append(f"V3 thiếu {f.name}")
                continue
            xl = pd.read_excel(f, sheet_name="All")
            for kind, col in (("Sim", f"{q}_sim"), ("Theory", f"{q}_theory")):
                xcol = [c for c in xl.columns if c.startswith(f"{q}_{kind}")]
                if len(xl) != len(fresh) or not xcol:
                    errs.append(f"V3 {f.name}: số dòng/cột không khớp dữ liệu tính lại")
                    break
                a, b = fresh[col].astype(float), xl[xcol[0]].astype(float)
                bad = int(((a - b).abs() > 1e-9 * a.abs().clip(lower=1)).sum())
                if bad:
                    errs.append(f"V3 {f.name}: {bad} ô {q}_{kind} KHÔNG khớp tính lại từ Raw_Merged (số liệu bị sửa tay?)")
    return errs


def _v4(p) -> list:
    junk = [f for f in p.results.rglob("*") if f.name.startswith("~$") or f.suffix in (".tmp", ".bak") or "_work" in f.parts]
    return [f"V4 file rác: {f.relative_to(p.dir)}" for f in junk[:20]]


def validate(p):
    p.require_stage("ANALYZED", "VALIDATED")
    errs = []
    for name, fn in (("V1 đủ job", _v1), ("V2 zero-null Excel", _v2), ("V3 chống số liệu giả", _v3), ("V4 không rác", _v4)):
        ui.step(name)
        e = fn(p)
        if e:
            for x in e[:30]:
                ui.fail(x)
        else:
            ui.ok("đạt")
        errs += e
    if errs:
        raise GateError(f"Kiểm định KHÔNG đạt ({len(errs)} lỗi). Sửa nguyên nhân gốc rồi chạy lại analyze + validate.")
    p.set_stage("VALIDATED", "V1-V4 đạt")

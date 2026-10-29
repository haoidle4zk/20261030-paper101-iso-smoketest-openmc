"""ANALYZE (tuỳ chọn; analysis.type = none thì bỏ qua): tính đại lượng che chắn từ Raw_Merged -> Excel trong results/01_Summary_Excel_Results/<sub>/.

analysis.type = attenuation  (chuẩn, cấu hình trong project.yaml):
  MAC = ln(I0/I) / (rho * x)      LAC = MAC * rho
  HVL = ln2 / LAC   TVL = ln10 / LAC   MFP = 1 / LAC   RPE = (1 - I/I0) * 100
  Lý thuyết lấy từ theory_csv (cột sample, energy_kev, mac_cm2_g), RPE lý thuyết = (1 - e^(-LAC*x))*100
  Sai số % = (Sim - Lý thuyết) / Lý thuyết * 100

  LUẬT SỐ LIỆU:
  - I = 0 (công cụ ghi nhận cản 100%): Sim = 0 cho MAC/LAC/HVL/TVL/MFP, Note = "I=0", sai số = -100%.
  - Thiếu I0, thiếu lý thuyết, lý thuyết <= 0 -> DỪNG (GateError). Không bao giờ đắp số.

analysis.type = custom: chạy analysis/analyze.py <đường dẫn project>; script đó phải ghi Excel vào
  results/01_Summary_Excel_Results/<sub>/ và tuân thủ cùng luật số liệu.
"""

import math
import subprocess
import sys

import pandas as pd

from . import ui
from .ui import GateError

QUANTITIES = ["MAC", "LAC", "HVL", "TVL", "MFP", "RPE"]
UNITS = {"MAC": "cm2/g", "LAC": "1/cm", "HVL": "cm", "TVL": "cm", "MFP": "cm", "RPE": "%"}
OUT = "01_Summary_Excel_Results"


def _sim_values(I: float, I0: float, rho: float, x: float) -> tuple:
    if I == 0:
        return {"MAC": 0.0, "LAC": 0.0, "HVL": 0.0, "TVL": 0.0, "MFP": 0.0, "RPE": 100.0}, "I=0"
    mac = math.log(I0 / I) / (rho * x)
    lac = mac * rho
    if lac <= 0:
        return {"MAC": mac, "LAC": lac, "HVL": 0.0, "TVL": 0.0, "MFP": 0.0, "RPE": (1 - I / I0) * 100}, "I>=I0"
    return {"MAC": mac, "LAC": lac, "HVL": math.log(2) / lac, "TVL": math.log(10) / lac, "MFP": 1 / lac,
            "RPE": (1 - I / I0) * 100}, "-"


def _theory_values(mac: float, rho: float, x: float) -> dict:
    lac = mac * rho
    return {"MAC": mac, "LAC": lac, "HVL": math.log(2) / lac, "TVL": math.log(10) / lac, "MFP": 1 / lac,
            "RPE": (1 - math.exp(-lac * x)) * 100}


def compute_attenuation(p, sub: dict) -> pd.DataFrame:
    a = p.cfg["analysis"]
    n = sub["name"]
    raw = pd.read_csv(p.results / "02_Flux_Data" / n / f"Raw_Merged_{n}.csv")
    col = a["intensity_column"]
    if col not in raw.columns:
        raise GateError(f"{n}: cột intensity_column '{col}' không có trong Raw_Merged. Có: {list(raw.columns)}")
    theory = pd.read_csv(p.dir / a["theory_csv"])
    theory["sample"] = theory["sample"].str.upper()
    dup = theory.assign(e=theory["energy_kev"].astype(float).round(6)).duplicated(["sample", "e"], keep=False)
    if dup.any():
        raise GateError(f"{a['theory_csv']}: trùng (sample, energy_kev) ở {int(dup.sum())} dòng — mỗi cặp chỉ được một giá trị")
    tmap = {(r.sample, round(float(r.energy_kev), 6)): float(r.mac_cm2_g) for r in theory.itertuples()}
    rho_map = {k.upper(): float(v) for k, v in a["densities_g_cm3"].items()}

    i0cfg = a["i0"]
    if i0cfg["mode"] == "blank":
        bl = raw[raw["sample"].str.upper() == i0cfg["sample"].upper()]
        i0_map = {round(float(r["energy_kev"]), 6): float(r[col]) for _, r in bl.iterrows()}
        data = raw[raw["sample"].str.upper() != i0cfg["sample"].upper()]
    elif i0cfg["mode"] == "column":
        i0_map = None
        data = raw
    else:
        raise GateError("analysis.i0.mode phải là 'blank' hoặc 'column'")

    out, errs = [], []
    for _, r in data.iterrows():
        s, e, x = str(r["sample"]).upper(), round(float(r["energy_kev"]), 6), float(r["thickness_cm"])
        I = float(r[col])
        I0 = i0_map.get(e) if i0_map is not None else float(r[i0cfg["column"]])
        if I0 is None or I0 <= 0:
            errs.append(f"{r['job_id']}: không có I0 hợp lệ")
            continue
        if s not in rho_map:
            errs.append(f"{r['job_id']}: thiếu mật độ cho {s}")
            continue
        if (s, e) not in tmap or tmap[(s, e)] <= 0:
            errs.append(f"{r['job_id']}: thiếu lý thuyết MAC cho ({s}, {e} keV)")
            continue
        sim, note = _sim_values(I, I0, rho_map[s], x)
        th = _theory_values(tmap[(s, e)], rho_map[s], x)
        rec = {"job_id": r["job_id"], "sample": s, "thickness_cm": x, "energy_kev": e, "I0": I0, "I": I,
               "rho_g_cm3": rho_map[s], "note": note}
        for q in QUANTITIES:
            rec[f"{q}_sim"] = sim[q]
            rec[f"{q}_theory"] = th[q]
            rec[f"{q}_diff_pct"] = (sim[q] - th[q]) / th[q] * 100 if th[q] != 0 else -100.0
        out.append(rec)
    if errs:
        for x_ in errs[:20]:
            ui.fail(x_)
        raise GateError(f"{n}: {len(errs)} lỗi đầu vào phân tích")
    return pd.DataFrame(out).sort_values(["thickness_cm", "sample", "energy_kev"])


def write_excels(p, sub_name: str, df: pd.DataFrame):
    d = p.results / OUT / sub_name
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / f"Analysis_Long_{sub_name}.csv", index=False)
    for i, q in enumerate(QUANTITIES, 1):
        path = d / f"{i:02d}_{q}_{sub_name}.xlsx"
        cols = ["sample", "energy_kev", "I0", "I", f"{q}_sim", f"{q}_theory", f"{q}_diff_pct", "note"]
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            all_df = df[["thickness_cm"] + cols].rename(columns={
                f"{q}_sim": f"{q}_Sim ({UNITS[q]})", f"{q}_theory": f"{q}_Theory ({UNITS[q]})", f"{q}_diff_pct": "Diff_%"})
            all_df.to_excel(xw, sheet_name="All", index=False)
            for x, g in df.groupby("thickness_cm"):
                sheet = f"x={x:g}cm"[:31]
                g[cols].rename(columns={f"{q}_sim": f"{q}_Sim ({UNITS[q]})", f"{q}_theory": f"{q}_Theory ({UNITS[q]})",
                                        f"{q}_diff_pct": "Diff_%"}).to_excel(xw, sheet_name=sheet, index=False)
        ui.ok(f"{path.relative_to(p.dir)}")


def analyze(p):
    p.require_stage("HARVESTED", "ANALYZED")
    a = p.cfg.get("analysis") or {}
    kind = a.get("type") or "none"
    if kind == "none":
        ui.ok("analysis.type = none: không tính đại lượng — kết quả là dữ liệu thô Raw_Merged (đủ để bàn giao)")
    elif kind == "custom":
        script = p.dir / "analysis" / "analyze.py"
        if not script.exists():
            raise GateError("analysis.type=custom nhưng thiếu analysis/analyze.py")
        ui.step(f"Chạy {script.relative_to(p.dir)}")
        r = subprocess.run([sys.executable, str(script), str(p.dir)])
        if r.returncode != 0:
            raise GateError("analysis/analyze.py báo lỗi")
    elif kind == "attenuation":
        for sub in p.subprojects:
            ui.step(f"Phân tích suy giảm: {sub['name']}")
            write_excels(p, sub["name"], compute_attenuation(p, sub))
    else:
        raise GateError("analysis.type phải là 'none', 'attenuation' hoặc 'custom'")
    p.set_stage("ANALYZED", kind)

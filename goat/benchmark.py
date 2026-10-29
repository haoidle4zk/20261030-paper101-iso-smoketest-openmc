"""Báo cáo thời gian & tài nguyên: results/05_Execution_Logs_and_Benchmarks/Benchmark_Report.xlsx

Sheet:
  Summary     tổng quan: số job, CPU-giờ, thời gian thực cả đợt, RAM đỉnh, tỉ lệ mô phỏng / upload / setup
  Timeline    thời điểm và thời lượng từng stage (từ logs/state.json)
  Per_Job     từng job: thời gian chạy, CPU user/sys, % CPU, RAM đỉnh, thời gian upload, runner
  Per_Runner  từng runner: setup, quét Drive, mô phỏng, upload, tổng; cấu hình máy (CPU, số nhân, RAM)
  By_Energy / By_Sample / By_Thickness   thống kê thời gian chạy và RAM theo nhóm
"""

import glob
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import ui

LOGS = "05_Execution_Logs_and_Benchmarks"


def _runner_frames(p):
    recs, jobs = [], []
    for f in sorted(glob.glob(str(p.results / LOGS / "_runners" / "*.json"))):
        r = json.loads(Path(f).read_text(encoding="utf-8"))
        h = r.get("host") or {}
        recs.append({"account": r["account"], "runner": str(r["runner"]), "mode": r["mode"], "started_utc": r["started"],
                     "setup_s": r.get("setup_s"), "drive_index_s": r.get("index_s"), "sim_s": r.get("sim_s"),
                     "upload_s": r.get("upload_s"), "wall_s": r["wall_seconds"], "n_jobs": len(r["jobs"]),
                     "ok": sum(j["status"] == "OK" for j in r["jobs"]),
                     "errors": sum(j["status"].startswith(("ERROR", "FAILED")) for j in r["jobs"]),
                     "cpu_model": h.get("cpu_model"), "n_cpu": h.get("n_cpu"), "mem_total_gb": h.get("mem_total_gb")})
        for j in r["jobs"]:
            jobs.append({"job_id": j["job_id"], "status": j["status"], "upload_s": j.get("upload_s"),
                         "attempt_account": r["account"], "attempt_runner": str(r["runner"]), "mode": r["mode"]})
    return pd.DataFrame(recs), pd.DataFrame(jobs)


def _timeline(p) -> pd.DataFrame:
    hist = p.state()["history"]
    rows = []
    for i, h in enumerate(hist):
        t = datetime.strptime(h["time"], "%Y-%m-%d %H:%M:%S")
        nxt = datetime.strptime(hist[i + 1]["time"], "%Y-%m-%d %H:%M:%S") if i + 1 < len(hist) else None
        rows.append({"time": h["time"], "stage": h["to"], "note": h.get("note", "")[:120],
                     "minutes_in_stage": round((nxt - t).total_seconds() / 60, 1) if nxt else None})
    return pd.DataFrame(rows)


def build(p) -> Path:
    merged = pd.concat([pd.read_csv(p.results / "02_Flux_Data" / s["name"] / f"Raw_Merged_{s['name']}.csv")
                        for s in p.subprojects], ignore_index=True)
    cols = [c for c in ("job_id", "subproject", "sample", "thickness_cm", "energy_kev", "primaries", "account", "runner",
                        "runtime_s", "cpu_user_s", "cpu_sys_s", "cpu_pct", "peak_rss_mb") if c in merged.columns]
    per_job = merged[cols].drop_duplicates("job_id").copy()
    runners, attempts = _runner_frames(p)
    if not attempts.empty:
        ok = attempts[attempts.status == "OK"].drop_duplicates("job_id", keep="last")[["job_id", "upload_s"]]
        per_job = per_job.merge(ok, on="job_id", how="left")
        retried = attempts[attempts.status != "SKIP_ALREADY_ON_DRIVE"].groupby("job_id").size()
        per_job["attempts"] = per_job["job_id"].map(retried).fillna(1).astype(int)

    cpu = (per_job.get("cpu_user_s", 0).fillna(0) + per_job.get("cpu_sys_s", 0).fillna(0)) if "cpu_user_s" in per_job else None
    tl = _timeline(p)
    run_span = None
    t_run = tl[tl.stage == "RUNNING"]["time"].tolist()
    t_done = tl[tl.stage == "COMPLETE"]["time"].tolist()
    if t_run and t_done:
        run_span = (datetime.strptime(t_done[-1], "%Y-%m-%d %H:%M:%S") - datetime.strptime(t_run[-1], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    normal = runners[runners["mode"] == "normal"] if not runners.empty else runners

    def v(x, nd=2):
        return None if x is None or pd.isna(x) else round(float(x), nd)

    summary = [
        ("Project", p.name), ("Công cụ", p.tool), ("Số hạt mỗi job", p.cfg.get("primaries")),
        ("Số job", len(per_job)),
        ("Số runner (lượt chạy thường / cứu hộ)", f"{len(normal)} / {len(runners) - len(normal)}" if not runners.empty else "-"),
        ("Tổng thời gian mô phỏng (giờ, cộng mọi job)", v(per_job["runtime_s"].sum() / 3600, 3)),
        ("Tổng CPU-giờ (user+sys)", v(cpu.sum() / 3600, 3) if cpu is not None else "-"),
        ("Thời gian 1 job: trung bình / trung vị / lớn nhất (s)",
         f"{v(per_job.runtime_s.mean())} / {v(per_job.runtime_s.median())} / {v(per_job.runtime_s.max())}"),
        ("% CPU trung bình mỗi job", v(per_job["cpu_pct"].mean(), 1) if "cpu_pct" in per_job else "-"),
        ("RAM đỉnh lớn nhất / trung bình (MB)",
         f"{v(per_job.peak_rss_mb.max(), 1)} / {v(per_job.peak_rss_mb.mean(), 1)}" if "peak_rss_mb" in per_job else "-"),
        ("Thời gian thực cả đợt chạy RUNNING→COMPLETE (phút)", v(run_span, 1)),
        ("Runner: setup TB (s)", v(normal["setup_s"].mean(), 1) if "setup_s" in normal and normal["setup_s"].notna().any() else "-"),
        ("Runner: mô phỏng TB (s)", v(normal["sim_s"].mean(), 1) if not normal.empty else "-"),
        ("Runner: upload Drive TB (s)", v(normal["upload_s"].mean(), 1) if "upload_s" in normal and normal["upload_s"].notna().any() else "-"),
        ("Runner: tổng TB / lâu nhất (s)", f"{v(normal.wall_s.mean(), 1)} / {v(normal.wall_s.max(), 1)}" if not normal.empty else "-"),
        ("Job phải chạy lại (attempts>1)", int((per_job.get("attempts", pd.Series(dtype=int)) > 1).sum())),
        ("Máy runner", f"{normal.cpu_model.dropna().iloc[0]} | {normal.n_cpu.dropna().iloc[0]} nhân | {normal.mem_total_gb.dropna().iloc[0]} GB"
         if not normal.empty and normal["cpu_model"].notna().any() and (normal["cpu_model"] != "").any() else "-"),
    ]
    out = p.results / LOGS / "Benchmark_Report.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        pd.DataFrame(summary, columns=["Chỉ số", "Giá trị"]).to_excel(xw, sheet_name="Summary", index=False)
        tl.to_excel(xw, sheet_name="Timeline", index=False)
        per_job.sort_values("runtime_s", ascending=False).to_excel(xw, sheet_name="Per_Job", index=False)
        if not runners.empty:
            runners.sort_values(["mode", "account", "runner"]).to_excel(xw, sheet_name="Per_Runner", index=False)
        agg = {"runtime_s": ["count", "mean", "max"]}
        if "peak_rss_mb" in per_job:
            agg["peak_rss_mb"] = ["mean", "max"]
        for key, sheet in (("energy_kev", "By_Energy"), ("sample", "By_Sample"), ("thickness_cm", "By_Thickness")):
            if key in per_job:
                g = per_job.groupby(key).agg(agg).round(2)
                g.columns = ["_".join(c) for c in g.columns]
                g.to_excel(xw, sheet_name=sheet)
    for k, val in summary:
        ui.info(f"{k}: {val}")
    ui.ok(f"{out.relative_to(p.dir)}")
    return out

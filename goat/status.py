"""STATUS / RESCUE / WATCH: đếm tiến độ theo manifest, phát hiện job thiếu, cứu hộ runner-to-runner.

Nguồn sự thật DUY NHẤT về "job đã xong": file <job>.csv có trên Drive 02_Flux_Data/<sub>/<SAMPLE>/.
"""

import csv
import json
import math
import time

from . import creds, deploy, drive, github, manifest, plan, ui
from .ui import GateError

RESCUE_CHUNK = 1500  # số job_id tối đa trong một lần dispatch (giới hạn độ dài input của GitHub)


def _tree(p) -> dict:
    with open(p.drive_folders_path, "r", encoding="utf-8") as f:
        return json.load(f)


def done_jobs(p, svc=None) -> set:
    folders = {fid: sub for sub, samples in _tree(p)["flux_samples"].items() for fid in samples.values()}
    done = set()
    for fid, files in drive.list_many(list(folders)).items():
        done |= {f"{folders[fid]}/{f['name'][:-4]}" for f in files if f["name"].endswith(".csv")}
    return done


def failed_logs(p, svc) -> dict:
    out = {}
    for sub, tiers in _tree(p)["sub"].items():
        for f in drive.list_children(svc, tiers["05_Execution_Logs_and_Benchmarks"]):
            if f["name"].endswith(".FAILED.log"):
                out[f"{sub}/{f['name'][:-len('.FAILED.log')]}"] = f["id"]
    return out


def runner_activity(p) -> dict:
    """{account: {"active": n_inprogress_or_queued, "failed": n, "success": n}} cho repo của project."""
    accs = creds.accounts()
    res = {}
    for acc in plan.read(p.plan_path):
        if acc not in accs:
            continue
        runs = github.list_runs(acc, accs[acc]["token"], p.repo)
        a = {"active": 0, "failed": 0, "success": 0}
        for r in runs:
            if r["status"] in ("queued", "in_progress", "waiting", "pending", "requested"):
                a["active"] += 1
            elif r.get("conclusion") == "success":
                a["success"] += 1
            elif r["status"] == "completed":
                a["failed"] += 1
        res[acc] = a
    return res


def status(p, quiet: bool = False) -> dict:
    p.require_stage("RUNNING", "COMPLETE")
    rows = manifest.read(p.manifest_path)
    ids = [r["job_id"] for r in rows]
    svc = drive.service()
    done = done_jobs(p, svc)
    extra = sorted(done - set(ids))
    missing = [j for j in ids if j not in done]
    failed = failed_logs(p, svc)
    failed_now = sorted(j for j in failed if j in missing)
    act = runner_activity(p)
    active = sum(a["active"] for a in act.values())

    with open(p.logs / "checklist.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job_id", "status"])
        for j in ids:
            w.writerow([j, "DONE" if j in done else ("FAILED_LAST_TRY" if j in failed else "MISSING")])
    snap = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "total": len(ids), "done": len(ids) - len(missing),
            "missing": len(missing), "failed_last_try": len(failed_now), "runs_active": active,
            "accounts": act, "unexpected_files": extra[:50]}
    with open(p.logs / "status.json", "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1)

    if not quiet:
        pct = 100.0 * snap["done"] / max(1, snap["total"])
        ui.step(f"TIẾN ĐỘ {p.name}")
        ui.info(f"Xong {snap['done']}/{snap['total']} ({pct:.1f}%) | Thiếu {snap['missing']} | "
                f"Lỗi lần chạy gần nhất {snap['failed_last_try']} | Run đang chạy/chờ {active}")
        for acc, a in act.items():
            ui.info(f"  {acc:<18} active={a['active']} success={a['success']} failed={a['failed']}")
        if extra:
            ui.warn(f"{len(extra)} file CSV trên Drive không có trong manifest (sai tên?): {extra[:5]}")
        if failed_now:
            ui.warn(f"Job lỗi (xem log .FAILED.log trên Drive 05_...): {failed_now[:10]}")
    if snap["missing"] == 0 and p.stage == "RUNNING":
        p.set_stage("COMPLETE", "Drive có đủ 100% CSV theo manifest")
    snap["missing_ids"] = missing
    return snap


def rescue(p, missing: list, round_no: int) -> int:
    """Dispatch các job thiếu sang tài khoản đang rảnh. Trả về số job đã dispatch."""
    if not missing:
        return 0
    accs = creds.accounts()
    act = runner_activity(p)
    idle = [a for a, s in act.items() if s["active"] == 0 and a in accs]
    if not idle:
        ui.warn("Không có tài khoản rảnh để cứu hộ; thử lại ở vòng sau.")
        return 0
    per_acc = min(RESCUE_CHUNK, math.ceil(len(missing) / len(idle)))
    chunks = [missing[i:i + per_acc] for i in range(0, len(missing), per_acc)]
    sent = 0
    log = []
    for acc, chunk in zip(idle, chunks):
        ok, err = github.dispatch(acc, accs[acc]["token"], p.repo, deploy.WORKFLOW_FILE,
                                  {"mode": "rescue", "jobs": ",".join(chunk), "drive_root": p.cfg["drive"]["root_folder_id"]})
        (ui.ok if ok else ui.fail)(f"cứu hộ vòng {round_no}: {len(chunk)} job -> {acc} {'' if ok else err}")
        log.append({"account": acc, "jobs": len(chunk), "ok": ok})
        sent += len(chunk) if ok else 0
    if len(chunks) > len(idle):
        ui.warn(f"Còn {len(missing) - sent} job chưa có tài khoản rảnh; sẽ cứu hộ ở vòng sau.")
    hist_p = p.logs / "rescues.json"
    hist = json.loads(hist_p.read_text(encoding="utf-8")) if hist_p.exists() else []
    hist.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "round": round_no, "dispatch": log})
    hist_p.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    return sent


def rescue_rounds_done(p) -> int:
    hp = p.logs / "rescues.json"
    if not hp.exists():
        return 0
    return max((h["round"] for h in json.loads(hp.read_text(encoding="utf-8"))), default=0)


def watch(p, interval: int = 300, max_rounds: int = None):
    """Vòng lặp giám sát: in tiến độ; khi mọi run đã dừng mà còn thiếu -> cứu hộ; dừng khi đủ 100%
    hoặc khi hết số vòng cứu hộ cho phép (khi đó báo FAIL để con người xem log)."""
    max_rounds = max_rounds or int(p.cfg.get("max_rescue_rounds", 3))
    while True:
        snap = status(p)
        if snap["missing"] == 0:
            return True
        if snap["runs_active"] == 0:
            r = rescue_rounds_done(p) + 1
            if r > max_rounds:
                ui.fail(f"Đã cứu hộ {max_rounds} vòng mà vẫn thiếu {snap['missing']} job. "
                        "DỪNG. Đọc các file .FAILED.log trên Drive và báo người dùng; KHÔNG tự sửa số liệu.")
                return False
            rescue(p, snap["missing_ids"], r)
            time.sleep(90)
            continue
        time.sleep(interval)


def require_complete(p):
    if p.stage != "COMPLETE":
        raise GateError("Chưa đủ 100% job. Chạy: python -m goat watch <project>")

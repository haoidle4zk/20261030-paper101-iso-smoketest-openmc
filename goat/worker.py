"""Worker chạy TRÊN RUNNER GitHub Actions (không chạy ở máy trạm).

  python -m goat.worker --account <owner> --runner-id <n> [--mode rescue --jobs "sub/job,sub/job"]

Bundle trên runner (do goat deploy tạo) gồm: goat/, project.yaml, manifest.csv, plan.json,
drive_folders.json, sim/<sub>/..., parsers/ (nếu có).

Khởi động: quét Drive MỘT lần (CSV đã có, thư mục output, log) để không phải hỏi lại Drive cho từng job.
Mỗi job:
  1. <job>.csv đã có trên Drive -> bỏ qua (không chạy trùng).
  2. Thư mục riêng _work/<sub>/<job>/, chép input (+ run.copy_files); thay @PRIMARIES@ bằng số hạt.
  3. Chạy run.command (biến: {sim_dir} {input} {job} {primaries} {threads}) và ĐO tài nguyên:
     thời gian thực, CPU user/sys, % CPU, RAM đỉnh (RSS) của toàn bộ tiến trình mô phỏng.
  4. Parser đọc kết quả. Lỗi => job FAILED: chỉ upload <job>.FAILED.log, KHÔNG upload CSV.
  5. Thành công => upload 03_Simulation_Outputs/<sub>/<job>/*, 05_.../<sub>/<job>.log,
     rồi CUỐI CÙNG 02_Flux_Data/<sub>/<SAMPLE>/<job>.csv (có CSV = job trọn vẹn).
     Drive báo quá hạn mức (403/429) => chờ rồi thử lại tới ~10 phút; kết quả đã tính không bị bỏ.
Runner tự dừng nhận job mới khi gần hết timeout_minutes (chừa 20 phút) để kết thúc sạch.
"""

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from . import drive, manifest, parsers

SAFETY_MIN = 20
UPLOAD_ROUNDS = 3        # số vòng thử lại cả cụm upload của 1 job khi Drive quá tải
UPLOAD_COOLDOWN = 90     # giây chờ giữa các vòng


def _load(root: Path):
    with open(root / "project.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    rows = {r["job_id"]: r for r in manifest.read(root / "manifest.csv")}
    with open(root / "plan.json", "r", encoding="utf-8") as f:
        plan = json.load(f)
    with open(root / "drive_folders.json", "r", encoding="utf-8") as f:
        tree = json.load(f)
    return cfg, rows, plan, tree


def _write_csv(path: Path, rows: list):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def host_info() -> dict:
    info = {"os": platform.platform(), "n_cpu": os.cpu_count(), "cpu_model": "", "mem_total_gb": None}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu_model"] = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                info["mem_total_gb"] = round(int(line.split()[1]) / 1024 / 1024, 2)
                break
    except OSError:
        pass
    return info


def run_measured(cmd: str, cwd: Path, timeout: float) -> dict:
    """Chạy lệnh shell, đo thời gian thực + CPU + RAM đỉnh (Linux: os.wait4 trả rusage của cả cây tiến trình con)."""
    out_f, err_f = cwd / ".stdout.txt", cwd / ".stderr.txt"
    t0 = time.time()
    res = {"rc": None, "wall_s": 0.0, "cpu_user_s": None, "cpu_sys_s": None, "peak_rss_mb": None}
    with open(out_f, "w") as fo, open(err_f, "w") as fe:
        proc = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdout=fo, stderr=fe, stdin=subprocess.DEVNULL,
                                start_new_session=hasattr(os, "wait4"))
        if hasattr(os, "wait4"):
            while True:
                pid, status, ru = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    res["rc"] = os.waitstatus_to_exitcode(status)
                    res["cpu_user_s"], res["cpu_sys_s"] = round(ru.ru_utime, 3), round(ru.ru_stime, 3)
                    res["peak_rss_mb"] = round(ru.ru_maxrss / 1024, 1)  # Linux: KB
                    proc.returncode = res["rc"]
                    break
                if time.time() - t0 > timeout:
                    os.killpg(proc.pid, 9)
                    os.wait4(proc.pid, 0)
                    res["rc"] = proc.returncode = -999
                    break
                time.sleep(0.2)
        else:
            try:
                res["rc"] = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                res["rc"] = -999
    res["wall_s"] = round(time.time() - t0, 3)
    cpu = (res["cpu_user_s"] or 0) + (res["cpu_sys_s"] or 0)
    res["cpu_pct"] = round(100 * cpu / res["wall_s"], 1) if res["cpu_user_s"] is not None and res["wall_s"] > 0 else None
    res["stdout"] = out_f.read_text(errors="replace")[-20000:]
    res["stderr"] = err_f.read_text(errors="replace")[-20000:]
    if res["rc"] == -999:
        res["stderr"] += "\nTIMEOUT: vượt ngân sách thời gian runner"
    out_f.unlink(missing_ok=True)
    err_f.unlink(missing_ok=True)
    return res


class DriveIndex:
    """Ảnh chụp Drive lúc runner khởi động: CSV đã xong, thư mục output, log. Giảm ~2/3 số lần gọi API."""

    def __init__(self, svc, tree: dict, jobs: list):
        subs = {j["subproject"] for j in jobs}
        flux = {tree["flux_samples"][j["subproject"]][j["sample"]] for j in jobs}
        folders = list(flux) + [tree["sub"][s]["03_Simulation_Outputs"] for s in subs] + \
                  [tree["sub"][s]["05_Execution_Logs_and_Benchmarks"] for s in subs]
        listing = drive.list_many(folders, workers=4)
        self.files = {fid: {f["name"]: f["id"] for f in items} for fid, items in listing.items()}

    def has(self, folder_id: str, name: str) -> bool:
        return name in self.files.get(folder_id, {})

    def put(self, svc, local: Path, folder_id: str) -> str:
        known = self.files.setdefault(folder_id, {})
        fid = drive.create_or_update(svc, local, folder_id, known.get(local.name))
        known[local.name] = fid
        return fid

    def folder(self, svc, parent_id: str, name: str) -> str:
        known = self.files.setdefault(parent_id, {})
        if name not in known:
            known[name] = drive.create_folder(svc, parent_id, name)
            self.files[known[name]] = {}
        elif known[name] not in self.files:
            self.files[known[name]] = {f["name"]: f["id"] for f in drive.list_children(svc, known[name])}
        return known[name]


def _upload_patiently(fn, label: str):
    for k in range(1, UPLOAD_ROUNDS + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if k == UPLOAD_ROUNDS:
                raise
            print(f"  WAIT {label}: Drive lỗi ({str(e)[:80]}), chờ {UPLOAD_COOLDOWN}s rồi thử lại ({k}/{UPLOAD_ROUNDS})")
            time.sleep(UPLOAD_COOLDOWN)


def run_job(job: dict, cfg: dict, root: Path, svc, tree: dict, idx: DriveIndex, account: str, runner: str,
            deadline: float) -> dict:
    run = cfg["run"]
    sub, name = job["subproject"], job["job"]
    flux_folder = tree["flux_samples"][sub][job["sample"]]
    log_dir = tree["sub"][sub]["05_Execution_Logs_and_Benchmarks"]
    rec = {"job_id": job["job_id"], "status": "", "wall_s": 0.0}

    if idx.has(flux_folder, f"{name}.csv"):
        rec["status"] = "SKIP_ALREADY_ON_DRIVE"
        print(f"  SKIP {job['job_id']} (đã có trên Drive)")
        return rec

    sim_dir = (root / "sim" / sub).resolve()
    work = root / "_work" / sub / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    text = (sim_dir / job["input_file"]).read_text(encoding="utf-8", errors="replace")
    # Input soạn trên Windows có CRLF; FLUKA/Geant4 trên Linux đọc '\r' thành ký tự rác -> chuẩn hoá về LF.
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("@PRIMARIES@", str(cfg["primaries"]))
    (work / Path(job["input_file"]).name).write_text(text, encoding="utf-8", newline="\n")
    for pattern in run.get("copy_files") or []:
        for p in sim_dir.glob(pattern):
            shutil.copytree(p, work / p.name) if p.is_dir() else shutil.copy2(p, work / p.name)

    cmd = run["command"].format(sim_dir=str(sim_dir), input=Path(job["input_file"]).name, job=name,
                                primaries=cfg["primaries"], threads=os.cpu_count() or 2)
    print(f"  RUN  {job['job_id']} :: {cmd}")
    m = run_measured(cmd, work, max(60, deadline - time.time()))
    for k in ("wall_s", "cpu_user_s", "cpu_sys_s", "cpu_pct", "peak_rss_mb"):
        rec[k] = m[k]
    log_lines = [f"job_id: {job['job_id']}", f"account: {account} runner: {runner}", f"command: {cmd}",
                 f"returncode: {m['rc']}", f"wall_s: {m['wall_s']}", f"cpu_user_s: {m['cpu_user_s']}",
                 f"cpu_sys_s: {m['cpu_sys_s']}", f"cpu_pct: {m['cpu_pct']}", f"peak_rss_mb: {m['peak_rss_mb']}",
                 "----- stdout (tail) -----", m["stdout"], "----- stderr (tail) -----", m["stderr"]]

    rows, problem = None, None
    if m["rc"] != 0:
        problem = f"returncode {m['rc']}"
    else:
        try:
            rows = parsers.run_parser(run["parser"], work, job, run, root)
        except Exception as e:  # noqa: BLE001
            problem = f"parser: {e}"

    t_up = time.time()
    if problem:
        rec["status"] = "FAILED"
        lp = work / f"{name}.FAILED.log"
        # Kèm đuôi các file chẩn đoán của công cụ (vd FLUKA *.err/*.out) để đọc được nguyên nhân lỗi.
        for f in sorted(work.rglob("*")):
            if f.is_file() and f.suffix in (".err", ".out", ".log", ".lis"):
                log_lines += [f"----- {f.relative_to(work)} (tail) -----",
                              f.read_text(encoding="utf-8", errors="replace")[-3000:]]
        lp.write_text(f"STATUS: FAILED ({problem})\n" + "\n".join(log_lines), encoding="utf-8")
        _upload_patiently(lambda: idx.put(svc, lp, log_dir), job["job_id"])
        print(f"  FAIL {job['job_id']}: {problem}")
        shutil.rmtree(work, ignore_errors=True)
        return rec

    meta = {"job_id": job["job_id"], "subproject": sub, "job": name, "sample": job["sample"],
            "thickness_cm": job["thickness_cm"], "energy_kev": job["energy_kev"], "primaries": cfg["primaries"],
            "runtime_s": m["wall_s"], "cpu_user_s": m["cpu_user_s"], "cpu_sys_s": m["cpu_sys_s"],
            "cpu_pct": m["cpu_pct"], "peak_rss_mb": m["peak_rss_mb"], "account": account, "runner": runner}
    csv_path = work / f"{name}.csv"
    _write_csv(csv_path, [{**meta, **r} for r in rows])
    produced = [p for pat in (run.get("outputs") or []) for p in work.glob(pat) if p.is_file() and p != csv_path]
    lp = work / f"{name}.log"
    lp.write_text("STATUS: OK\n" + "\n".join(log_lines), encoding="utf-8")

    def _uploads():
        if produced:
            out_folder = idx.folder(svc, tree["sub"][sub]["03_Simulation_Outputs"], name)
            for p in produced:
                idx.put(svc, p, out_folder)
        idx.put(svc, lp, log_dir)
        idx.put(svc, csv_path, flux_folder)  # CUỐI CÙNG

    _upload_patiently(_uploads, job["job_id"])
    rec["upload_s"] = round(time.time() - t_up, 2)
    rec["status"] = "OK"
    print(f"  OK   {job['job_id']} (chạy {m['wall_s']}s, CPU {m['cpu_pct']}%, RAM {m['peak_rss_mb']} MB, upload {rec['upload_s']}s)")
    shutil.rmtree(work, ignore_errors=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--runner-id", required=True)
    ap.add_argument("--mode", default="normal", choices=["normal", "rescue"])
    ap.add_argument("--jobs", default="", help="rescue: danh sách job_id cách nhau dấu phẩy")
    a = ap.parse_args()

    root = Path.cwd()
    cfg, rows, plan, tree = _load(root)
    t_start = time.time()
    t0_env = os.environ.get("GOAT_T0")
    setup_s = round(t_start - float(t0_env), 1) if t0_env else None
    deadline = (float(t0_env) if t0_env else t_start) + (cfg["timeout_minutes"] - SAFETY_MIN) * 60
    drive.RETRY_TRIES = 9  # runner: chịu quota Drive lâu hơn (~8 phút backoff)

    if a.mode == "rescue":
        all_ids = [j.strip() for j in a.jobs.split(",") if j.strip()]
        n = int(cfg.get("runners_per_account", 10))
        my_ids = all_ids[int(a.runner_id) - 1::n]
    else:
        my_ids = plan.get(a.account, {}).get(str(a.runner_id), [])

    print("=" * 70)
    print(f"GOAT WORKER | {cfg['name']} | {a.account} #{a.runner_id} | mode={a.mode} | {len(my_ids)} job")
    print("=" * 70)
    unknown = [j for j in my_ids if j not in rows]
    if unknown:
        raise SystemExit(f"job_id không có trong manifest: {unknown[:5]}")

    svc = drive.service()
    records = []
    t_idx = time.time()
    idx = DriveIndex(svc, tree, [rows[j] for j in my_ids]) if my_ids else None
    index_s = round(time.time() - t_idx, 2)
    for i, jid in enumerate(my_ids, 1):
        if time.time() > deadline:
            print(f"  STOP: gần hết thời gian runner, còn {len(my_ids) - i + 1} job để cứu hộ.")
            records += [{"job_id": j, "status": "NOT_STARTED_TIMEOUT", "wall_s": 0} for j in my_ids[i - 1:]]
            break
        print(f"[{i}/{len(my_ids)}]")
        try:
            records.append(run_job(rows[jid], cfg, root, svc, tree, idx, a.account, a.runner_id, deadline))
        except Exception as e:  # noqa: BLE001  (lỗi hạ tầng: ghi nhận, chạy job tiếp theo)
            print(f"  ERROR {jid}: {e}")
            records.append({"job_id": jid, "status": f"ERROR: {str(e)[:200]}", "wall_s": 0})

    wall = round(time.time() - t_start, 1)
    summary = {"project": cfg["name"], "account": a.account, "runner": a.runner_id, "mode": a.mode,
               "started": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t_start)) + "Z",
               "setup_s": setup_s, "index_s": index_s, "wall_seconds": wall,
               "sim_s": round(sum(r.get("wall_s") or 0 for r in records), 1),
               "upload_s": round(sum(r.get("upload_s") or 0 for r in records), 1),
               "host": host_info(), "jobs": records}
    sp = root / f"runner_{a.account}_{a.runner_id}_{a.mode}_{int(t_start)}.json"
    sp.write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    bench = drive.ensure_folder(svc, tree["tiers"]["05_Execution_Logs_and_Benchmarks"], "_runners")
    drive.create_or_update(svc, sp, bench)
    okc = sum(r["status"] == "OK" for r in records)
    print(f"\nHOÀN TẤT RUNNER: OK={okc}/{len(records)} | setup {setup_s}s | mô phỏng {summary['sim_s']}s | "
          f"upload {summary['upload_s']}s | tổng {wall}s")


if __name__ == "__main__":
    main()

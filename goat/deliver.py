"""DELIVER: đẩy kết quả cuối lên đúng 5 tầng Drive, rồi kiểm tra lại trên Drive."""

import json
import shutil
import zipfile

from . import drive, manifest, paths, ui
from .status import _tree
from .ui import GateError


def deliver(p, purge_local: bool = False):
    p.require_stage("VALIDATED", "DELIVERED")
    tree = _tree(p)
    svc = drive.service()
    rows = manifest.read(p.manifest_path)

    errs = []
    ui.step("1/4 01_Summary_Excel_Results")
    for sub in p.subprojects:
        n = sub["name"]
        d = p.results / "01_Summary_Excel_Results" / n
        files = [f for f in sorted(d.iterdir()) if f.is_file()] if d.is_dir() else []
        errs += drive.upload_many(files, tree["sub"][n]["01_Summary_Excel_Results"])
        ui.ok(f"{n}: {len(files)} file")

    ui.step("2/4 04_Input_Decks: Input_Decks_<sub>.zip (đúng các file input đã chạy)")
    for sub in p.subprojects:
        n = sub["name"]
        files = sorted({r["input_file"] for r in rows if r["subproject"] == n})
        if sub.get("jobs_file"):
            files.append(sub["jobs_file"])
        z = p.results / "04_Input_Decks" / n / f"Input_Decks_{n}.zip"
        z.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in files:
                zf.write(p.sim / n / rel, arcname=rel)
        errs += drive.upload_many([z], tree["sub"][n]["04_Input_Decks"])
        ui.ok(f"{n}: {len(files)} file input -> {z.name} ({z.stat().st_size / 1e6:.1f} MB)")
    from . import benchmark
    benchmark.build(p)  # cập nhật Timeline tới bước bàn giao
    errs += drive.upload_many([p.results / "05_Execution_Logs_and_Benchmarks" / "Benchmark_Report.xlsx"],
                              tree["tiers"]["05_Execution_Logs_and_Benchmarks"])
    if errs:
        for e in errs[:10]:
            ui.fail(e)
        raise GateError(f"{len(errs)} file upload lỗi; chạy lại `goat deliver` (an toàn, ghi đè cùng tên)")
    drive.upload(svc, p.dir / paths.PROJECT_FILE, tree["root"])
    drive.upload(svc, p.manifest_path, tree["root"])

    ui.step("3/4 05_Execution_Logs_and_Benchmarks/_goat")
    goat_logs = drive.ensure_folder(svc, tree["tiers"]["05_Execution_Logs_and_Benchmarks"], "_goat")
    for f in sorted(p.logs.glob("*")):
        if f.is_file() and f.name != paths.DRIVE_FOLDERS_FILE:
            drive.upload(svc, f, goat_logs)
    ui.ok("đã upload state/status/checklist/rescues/deploy logs")

    ui.step("4/4 Kiểm tra lại trên Drive")
    errs = []
    for sub in p.subprojects:
        n = sub["name"]
        d01 = p.results / "01_Summary_Excel_Results" / n
        local = {f.name for f in d01.iterdir() if f.is_file()} if d01.is_dir() else set()
        remote = {f["name"] for f in drive.list_children(svc, tree["sub"][n]["01_Summary_Excel_Results"])}
        if local - remote:
            errs.append(f"{n}: Drive thiếu {sorted(local - remote)}")
        if not drive.list_children(svc, tree["sub"][n]["02_Flux_Data"], name=f"Raw_Merged_{n}.csv"):
            errs.append(f"{n}: Drive thiếu Raw_Merged_{n}.csv")
    if errs:
        for e in errs:
            ui.fail(e)
        raise GateError("Bàn giao chưa đủ trên Drive")
    ui.ok(f"https://drive.google.com/drive/folders/{tree['root']}")
    if p.drive_only or purge_local:
        # chỉ xoá SAU KHI đã kiểm tra Drive có đủ (bước 4/4 ở trên)
        for d in (p.results, p.deploy_dir):
            shutil.rmtree(d, ignore_errors=True)
        ui.ok("storage=drive_only: đã xoá results/ và .deploy/ ở máy. Xem lại: python -m goat fetch <project>")
    p.set_stage("DELIVERED", json.dumps({"drive_root": tree["root"], "local_copy": not (p.drive_only or purge_local)}))

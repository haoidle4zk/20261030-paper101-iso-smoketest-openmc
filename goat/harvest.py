"""HARVEST: gom kết quả từ Drive, gộp Raw_Merged theo subproject, đối chiếu 1-1 với manifest, lập benchmark.
FETCH:   tải thêm về máy bất kỳ lúc nào sau đó (CSV lẻ, output thô, log, input).

Hai chế độ harvest:
  mặc định        : tải từng <job>.csv về results/02_…, tải output thô về results/03_… (nếu harvest.download_outputs)
  --keep-on-drive : KHÔNG lưu file lẻ xuống máy. CSV được đọc thẳng vào bộ nhớ để gộp; trên máy chỉ có
                    Raw_Merged_<sub>.csv + benchmark (đủ cho analyze/validate). Mọi file lẻ vẫn nguyên trên Drive,
                    tải sau bằng `goat fetch`.
"""

import csv
import io
from pathlib import Path

from . import drive, manifest, ui
from .status import _tree
from .ui import GateError

FLUX = "02_Flux_Data"
OUTS = "03_Simulation_Outputs"
INPS = "04_Input_Decks"
LOGS = "05_Execution_Logs_and_Benchmarks"


def _read_rows(p: Path) -> list:
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def _write(p: Path, rows: list):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _fail_on(errs: list, what: str):
    if errs:
        for e in errs[:10]:
            ui.fail(e)
        raise GateError(f"{len(errs)} {what} lỗi; chạy lại lệnh (phần đã xong được bỏ qua)")


def harvest(p, keep_on_drive: bool = False):
    p.require_stage("COMPLETE", "HARVESTED")
    keep_on_drive = keep_on_drive or p.drive_only
    rows = manifest.read(p.manifest_path)
    tree = _tree(p)
    svc = drive.service()
    mode = "giữ file lẻ trên Drive" if keep_on_drive else "tải đầy đủ về máy"

    ui.step(f"1/4 Lấy CSV kết quả từ Drive ({mode}, 16 luồng)")
    folders = {fid: (sub, sample) for sub, samples in tree["flux_samples"].items() for sample, fid in samples.items()}
    listing = drive.list_many(list(folders))
    found = [(f, folders[fid]) for fid, files in listing.items() for f in files if f["name"].endswith(".csv")]
    ui.info(f"Drive có {len(found)} file CSV")
    data = {}  # (sub, tên file) -> list[dict]
    if keep_on_drive:
        raw = drive.read_many([f["id"] for f, _ in found])
        for f, (sub, _) in found:
            data[(sub, f["name"])] = list(csv.DictReader(io.StringIO(raw[f["id"]].decode("utf-8", errors="replace"))))
        ui.ok(f"đọc {len(data)} file vào bộ nhớ (không ghi file lẻ ra máy)")
    else:
        queue = [(f["id"], p.results / FLUX / sub / sample / f["name"], f.get("size")) for f, (sub, sample) in found]
        got, skipped, errs = drive.download_many(queue, label="CSV")
        _fail_on(errs, "file CSV tải")
        for f, (sub, sample) in found:
            data[(sub, f["name"])] = _read_rows(p.results / FLUX / sub / sample / f["name"])
        ui.ok(f"tải mới {got} file, bỏ qua {skipped} file đã có")

    ui.step("2/4 Đối chiếu 1-1 với manifest và gộp Raw_Merged")
    problems = []
    for sub in p.subprojects:
        n = sub["name"]
        merged = []
        for r in (x for x in rows if x["subproject"] == n):
            d = data.get((n, f"{r['job']}.csv"))
            if d is None:
                problems.append(f"thiếu {r['job_id']}")
                continue
            if not d:
                problems.append(f"rỗng {r['job_id']}")
                continue
            if any(x.get("job_id") != r["job_id"] for x in d):
                problems.append(f"job_id trong file không khớp: {r['job']}.csv")
            merged.extend(d)
        out = p.results / FLUX / n / f"Raw_Merged_{n}.csv"
        if merged:
            out.parent.mkdir(parents=True, exist_ok=True)
            _write(out, merged)
            drive.upload(svc, out, tree["sub"][n][FLUX])
            ui.ok(f"{n}: {len(merged)} dòng -> {out.relative_to(p.dir)} (đã upload Drive)")
    if problems:
        for x in problems[:20]:
            ui.fail(x)
        raise GateError(f"{len(problems)} lỗi đối chiếu; KHÔNG được phân tích khi dữ liệu chưa đủ")

    ui.step("3/4 Output thô (03_Simulation_Outputs)")
    if keep_on_drive:
        ui.info("giữ trên Drive. Tải sau: python -m goat fetch <project> --what outputs")
    elif not (p.cfg.get("harvest") or {}).get("download_outputs", True):
        ui.info("giữ trên Drive (project.yaml: harvest.download_outputs = false). Tải sau: goat fetch --what outputs")
    else:
        fetch(p, ["outputs"], tree=tree, _from_harvest=True)

    ui.step("4/4 Log runner, benchmark thời gian & tài nguyên")
    fetch(p, ["runners"], tree=tree, _from_harvest=True)
    keep = ("job_id", "account", "runner", "runtime_s", "cpu_user_s", "cpu_sys_s", "cpu_pct", "peak_rss_mb")
    for sub in p.subprojects:
        n = sub["name"]
        merged = _read_rows(p.results / FLUX / n / f"Raw_Merged_{n}.csv")
        bp = p.results / LOGS / n / f"benchmark_{n}.csv"
        bp.parent.mkdir(parents=True, exist_ok=True)
        _write(bp, [{k: r.get(k, "") for k in keep} for r in merged])
        drive.upload(svc, bp, tree["sub"][n][LOGS])
    from . import benchmark
    rep = benchmark.build(p)
    drive.upload(svc, rep, tree["tiers"][LOGS])
    p.set_stage("HARVESTED", f"{len(rows)} jobs merged; {mode}")


FETCH_WHAT = {
    "csv": "CSV kết quả từng job (02_Flux_Data/<sub>/<SAMPLE>/)",
    "outputs": "output thô từng job (03_Simulation_Outputs/<sub>/<job>/)",
    "inputs": "file input đã chạy (04_Input_Decks/<sub>/)",
    "logs": "log từng job (05_…/<sub>/<job>.log)",
    "runners": "tóm tắt từng runner (05_…/_runners/*.json)",
}


def fetch(p, what: list, tree=None, _from_harvest: bool = False):
    """Tải về results/ những phần đang chỉ có trên Drive. Chạy lại an toàn (bỏ qua file đã có cùng kích thước)."""
    if not _from_harvest:
        p.require_stage("COMPLETE", "HARVESTED", "ANALYZED", "VALIDATED", "DELIVERED", "ARCHIVED")
    tree = tree or _tree(p)
    what = list(FETCH_WHAT) if "all" in what else what
    for w in what:
        if w not in FETCH_WHAT:
            raise GateError(f"--what phải thuộc {list(FETCH_WHAT) + ['all']}")
        roots = []
        if w == "runners":
            f = drive.list_children(drive.thread_service(), tree["tiers"][LOGS], folders_only=True, name="_runners")
            roots = [(f[0]["id"], p.results / LOGS / "_runners")] if f else []
        else:
            tier = {"csv": FLUX, "outputs": OUTS, "inputs": INPS, "logs": LOGS}[w]
            roots = [(tree["sub"][s["name"]][tier], p.results / tier / s["name"]) for s in p.subprojects]
        queue = []
        for fid, local in roots:
            for file_id, rel, size in drive.walk_tree(fid):
                if w == "csv" and len(rel.parts) < 2:
                    continue  # Raw_Merged ở cấp subproject đã có sẵn
                if w == "logs" and not rel.name.endswith(".log"):
                    continue
                queue.append((file_id, local / rel, size))
        mb = sum(int(s or 0) for _, _, s in queue) / 1e6
        if not _from_harvest or w == "outputs":
            ui.info(f"{FETCH_WHAT[w]}: {len(queue)} file, {mb:.1f} MB")
        got, skipped, errs = drive.download_many(queue, label=w)
        _fail_on(errs, f"file {w} tải")
        ui.ok(f"{w}: tải mới {got}, bỏ qua {skipped} file đã có")

"""Bước PREPARE (kiểm tra + manifest + plan + cây Drive) và DEPLOY (đóng gói + push + dispatch)."""

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import creds, drive, github, manifest, paths, plan, registry, ui
from .ui import GateError

WORKFLOW_FILE = "goat.yml"
MAX_FILE_MB = 95
PUSH_PARALLEL = 6  # bundle FLUKA/OpenMC ~300 MB: đẩy tuần tự 12 tài khoản mất 15+ phút
SKIP_DIRS = {"build", "_work", "__pycache__", ".git", ".vs", "out"}


def usable_accounts() -> dict:
    """Tài khoản ACTIVE và token kiểm tra đạt."""
    good = {}
    for name, info in creds.accounts().items():
        ok, msg = github.check_account(name, info["token"])
        (ui.ok if ok else ui.warn)(f"{name:<18} {msg}")
        if ok:
            good[name] = info
    return good


def prepare(p):
    p.require_stage("CREATED", "READY")

    ui.step("1/4 Kiểm tra project.yaml")
    errs = p.validate_cfg()
    if errs:
        for e in errs:
            ui.fail(e)
        raise GateError("project.yaml chưa hợp lệ")
    ui.ok("project.yaml hợp lệ")

    ui.step("2/4 Sinh manifest (danh sách job đầy đủ)")
    rows, errs = manifest.build(p)
    if errs:
        for e in errs:
            ui.fail(e)
        raise GateError("manifest lỗi")
    manifest.write(p.manifest_path, rows)
    per_sub = {}
    for r in rows:
        per_sub[r["subproject"]] = per_sub.get(r["subproject"], 0) + 1
    ui.ok(f"{len(rows)} job -> {p.manifest_path.relative_to(p.dir)}  {per_sub}")

    ui.step("3/4 Kiểm tra tài khoản GitHub & chia job")
    accs = usable_accounts()
    if not accs:
        raise GateError("Không có tài khoản GitHub dùng được")
    pl = plan.build(rows, sorted(accs), p.cfg["runners_per_account"])
    plan.write(p.plan_path, pl)
    per_runner = max(len(v) for a in pl.values() for v in a.values())
    ui.ok(f"{len(accs)} tài khoản x {p.cfg['runners_per_account']} runner; tối đa {per_runner} job/runner")

    ui.step("4/4 Tạo cây thư mục Google Drive")
    svc = drive.service()
    tree = drive.init_project_tree(svc, p)
    with open(p.drive_folders_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=1)
    p.cfg["drive"]["root_folder_id"] = tree["root"]
    p.save_cfg()
    ui.ok(f"Drive root: https://drive.google.com/drive/folders/{tree['root']}")

    for t in paths.TIERS:
        for sub in p.subprojects:
            (p.results / t / sub["name"]).mkdir(parents=True, exist_ok=True)
    p.set_stage("READY", f"{len(rows)} jobs, {len(accs)} accounts")


def _copy_tree(src: Path, dst: Path):
    for item in src.iterdir():
        if item.name in SKIP_DIRS:
            continue
        if item.is_dir():
            _copy_tree(item, dst / item.name)
        else:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst / item.name)


def build_bundle(p) -> Path:
    b = p.deploy_dir / "bundle"
    if b.exists():
        shutil.rmtree(b, onerror=lambda f, path, e: (Path(path).chmod(0o700), f(path)))
    b.mkdir(parents=True)
    _copy_tree(paths.GOAT_PKG, b / "goat")
    _copy_tree(p.sim, b / "sim")
    if (p.dir / "parsers").is_dir():
        _copy_tree(p.dir / "parsers", b / "parsers")
    shutil.copy2(p.dir / paths.PROJECT_FILE, b / "project.yaml")
    shutil.copy2(p.manifest_path, b / "manifest.csv")
    shutil.copy2(p.plan_path, b / "plan.json")
    shutil.copy2(p.drive_folders_path, b / "drive_folders.json")

    tool = p.tool.lower()
    tpl = (paths.TEMPLATES / "workflows" / f"{tool}.yml").read_text(encoding="utf-8")
    runners = ", ".join(str(i) for i in range(1, p.cfg["runners_per_account"] + 1))
    wf = (tpl.replace("__PROJECT__", p.name).replace("__RUNNERS__", runners)
             .replace("__TIMEOUT__", str(p.cfg["timeout_minutes"])))
    (b / ".github" / "workflows").mkdir(parents=True)
    (b / ".github" / "workflows" / WORKFLOW_FILE).write_text(wf, encoding="utf-8")

    pkg = paths.BINARIES / tool
    if pkg.is_dir():
        _copy_tree(pkg, b / "packages")
    (b / ".gitignore").write_text("_work/\nbuild/\n__pycache__/\n", encoding="utf-8")

    big = [f for f in b.rglob("*") if f.is_file() and f.stat().st_size > MAX_FILE_MB * 1024 * 1024]
    if big:
        raise GateError(f"File > {MAX_FILE_MB}MB không push được lên GitHub: {[str(f.relative_to(b)) for f in big]}")
    leaked = [f for f in b.rglob("*") if f.is_file() and f.name in ("accounts.json", "gdrive_oauth.json")]
    if leaked:
        raise GateError(f"Bundle chứa file bí mật, dừng ngay: {leaked}")
    return b


def deploy(p, allow_concurrent: bool = False):
    p.require_stage("READY")
    others = [n for n in registry.running_projects() if n != p.name]
    if others and not allow_concurrent:
        raise GateError(f"Đang có project RUNNING: {others}. Chạy song song sẽ tranh runner. "
                        "Chờ xong, hoặc thêm --allow-concurrent nếu người dùng đã đồng ý.")

    ui.step("1/3 Đóng gói bundle")
    b = build_bundle(p)
    size_mb = sum(f.stat().st_size for f in b.rglob("*") if f.is_file()) / 1e6
    ui.ok(f"bundle {size_mb:.1f} MB tại {b.relative_to(p.dir)}")

    pl = plan.read(p.plan_path)
    accs = creds.accounts()
    oauth = creds.gdrive_oauth_str()
    root_id = p.cfg["drive"]["root_folder_id"]
    log = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "repo": p.repo, "accounts": {}}

    ui.step(f"2/3 Push lên {len(pl)} tài khoản (repo {p.repo}, {PUSH_PARALLEL} tài khoản song song)")
    github.init_bundle(b)

    def _push(acc):
        if acc not in accs:
            return acc, "not active", "không còn ACTIVE trong secrets/accounts.json"
        tok = accs[acc]["token"]
        if not github.ensure_repo(acc, tok, p.repo):
            return acc, "repo fail", "không tạo được repo"
        if not github.set_secret(acc, tok, p.repo, "GDRIVE_OAUTH_JSON", oauth):
            return acc, "secret fail", "không nạp được secret"
        ok, err = github.push_bundle(b, acc, tok, p.repo)
        return (acc, None, None) if ok else (acc, f"push fail {err}", f"push lỗi {err}")

    pushed = []
    with ThreadPoolExecutor(max_workers=PUSH_PARALLEL) as ex:
        for acc, state, msg in ex.map(_push, list(pl)):
            if state:
                ui.fail(f"{acc}: {msg}")
                log["accounts"][acc] = state
            else:
                ui.ok(f"{acc}: push xong")
                pushed.append(acc)

    time.sleep(5)
    ui.step("3/3 Dispatch workflow")
    started = []
    for acc in pushed:
        ok, err = github.dispatch(acc, accs[acc]["token"], p.repo, WORKFLOW_FILE,
                                  {"mode": "normal", "jobs": "", "drive_root": root_id})
        (ui.ok if ok else ui.fail)(f"{acc}: {'dispatch OK' if ok else err}")
        log["accounts"][acc] = "dispatched" if ok else f"dispatch fail {err}"
        if ok:
            started.append(acc)

    p.logs.mkdir(exist_ok=True)
    # Deploy lại (sau khi sửa lỗi) bắt đầu đếm vòng cứu hộ từ 0; lịch sử cũ được giữ trong log deploy này.
    hist_p = p.logs / "rescues.json"
    if started and hist_p.exists():
        log["previous_rescues"] = json.loads(hist_p.read_text(encoding="utf-8"))
        hist_p.unlink()
    with open(p.logs / f"deploy_{time.strftime('%Y%m%d_%H%M%S')}.json", "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    if not started:
        raise GateError("Không tài khoản nào khởi chạy được")
    if p.drive_only:
        shutil.rmtree(p.deploy_dir, ignore_errors=True)
        ui.ok("storage=drive_only: đã xoá bundle .deploy/ ở máy (cứu hộ chỉ cần dispatch, không cần bundle)")
    p.set_stage("RUNNING", f"dispatched {len(started)}/{len(pl)} accounts")
    if len(started) < len(pl):
        ui.warn(f"{len(pl) - len(started)} tài khoản không chạy; job của chúng sẽ được cứu hộ ở bước watch.")

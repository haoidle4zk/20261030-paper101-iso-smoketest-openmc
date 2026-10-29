"""CLI DUY NHẤT của hệ thống:  python -m goat <lệnh> ...

Mỗi lệnh in "KẾT QUẢ: OK" hoặc "KẾT QUẢ: FAIL" ở dòng cuối và trả mã thoát 0 / 1.
Agent vận hành PHẢI đọc dòng KẾT QUẢ; FAIL => dừng, báo người dùng, không tự chế cách vòng qua.
"""

import argparse
import json
import shutil
import sys

from . import paths, project, registry, ui
from .ui import GateError


def _p(name: str) -> project.Project:
    return project.Project(name)


def _clean_p(name: str) -> project.Project:
    """Project cho các lệnh đổi stage: chặn nếu thư mục gốc hoặc project còn rác."""
    from . import clean
    p = project.Project(name)
    clean.require_clean(p.dir)
    return p


def cmd_clean(a):
    from . import clean
    clean.clean(dry_run=a.dry_run)


def cmd_doctor(a):
    from . import doctor
    if not doctor.run(online=not a.offline):
        raise GateError("Máy này chưa đủ điều kiện vận hành (xem các dòng [FAIL])")


def cmd_accounts(a):
    from . import deploy
    ui.step("Kiểm tra tài khoản GitHub ACTIVE")
    n = len(deploy.usable_accounts())
    if n == 0:
        raise GateError("Không có tài khoản dùng được")


def cmd_auth_drive(a):
    from . import portable
    portable.auth_drive(client_secret=a.client_secret, force=a.force)


def cmd_export(a):
    from . import portable
    portable.export(out=a.out, with_oauth_client=a.with_oauth_client)


def cmd_detect(a):
    from . import detect
    detect.detect(a.folder, into=a.into, sub=a.sub, thickness_unit=a.thickness_unit)


def cmd_new(a):
    from . import newproject
    name = newproject.create(a.project, a.variant, a.tool, a.date)
    ui.info(f"Bước tiếp: điền projects/{name}/project.yaml, đặt code+input vào projects/{name}/sim/<subproject>/")


def cmd_list(a):
    reg = registry.load()["projects"]
    names = sorted(set(project.list_projects()) | set(reg))
    print(f"{'PROJECT':<55} {'STAGE':<11} TOOL")
    for n in names:
        r = dict(reg.get(n, {}))
        if "stage" not in r:  # project chép từ nơi khác (vd project ví dụ) chưa có trong registry
            try:
                p = project.Project(n)
                r = {"stage": p.stage, "tool": p.tool}
            except Exception:  # noqa: BLE001
                pass
        print(f"{n:<55} {r.get('stage', '?'):<11} {r.get('tool', '')}")


def cmd_check(a):
    from . import manifest
    p = _p(a.project)
    errs = p.validate_cfg()
    if not errs:
        rows, errs = manifest.build(p)
        ui.ok(f"manifest dự kiến {len(rows)} job")
    if errs:
        for e in errs:
            ui.fail(e)
        raise GateError("project chưa sẵn sàng")
    ui.ok(f"stage hiện tại: {p.stage}")


def cmd_prepare(a):
    from . import deploy
    deploy.prepare(_clean_p(a.project))


def cmd_deploy(a):
    from . import deploy
    deploy.deploy(_clean_p(a.project), allow_concurrent=a.allow_concurrent)


def cmd_status(a):
    from . import status
    s = status.status(_p(a.project))
    if a.missing:
        print("\n".join(s["missing_ids"]))


def cmd_watch(a):
    from . import status
    if not status.watch(_p(a.project), interval=a.interval, max_rounds=a.max_rounds):
        raise GateError("Không đạt 100% sau số vòng cứu hộ cho phép")


def cmd_rescue(a):
    from . import status
    p = _p(a.project)
    s = status.status(p)
    if s["missing"] == 0:
        ui.ok("không còn job thiếu")
        return
    status.rescue(p, s["missing_ids"], status.rescue_rounds_done(p) + 1)


def cmd_cancel(a):
    from . import creds, github, plan
    p = _p(a.project)
    p.require_stage("RUNNING")
    accs = creds.accounts()
    for acc in plan.read(p.plan_path):
        if acc not in accs:
            continue
        for r in github.list_runs(acc, accs[acc]["token"], p.repo):
            if r["status"] in ("queued", "in_progress", "waiting", "pending"):
                ok = github.cancel_run(acc, accs[acc]["token"], p.repo, r["id"])
                (ui.ok if ok else ui.fail)(f"{acc} run {r['id']} cancel")
    p.set_stage("READY", f"cancelled: {a.reason}")


def cmd_harvest(a):
    from . import harvest
    harvest.harvest(_clean_p(a.project), keep_on_drive=a.keep_on_drive)


def cmd_fetch(a):
    from . import harvest
    harvest.fetch(_p(a.project), a.what)


def cmd_analyze(a):
    from . import analysis
    analysis.analyze(_clean_p(a.project))


def cmd_validate(a):
    from . import validate
    validate.validate(_clean_p(a.project))


def cmd_deliver(a):
    from . import deliver
    deliver.deliver(_clean_p(a.project), purge_local=a.purge_local)


def cmd_archive(a):
    p = _clean_p(a.project)
    p.require_stage("DELIVERED")
    if p.deploy_dir.exists():
        shutil.rmtree(p.deploy_dir, ignore_errors=True)
    p.set_stage("ARCHIVED", a.reason or "")
    ui.ok(f"{p.name}: đã đánh dấu ARCHIVED (giữ nguyên trong projects/, xoá .deploy/)")


def cmd_reopen(a):
    p = _p(a.project)
    cur, to = project.STAGES.index(p.stage), project.STAGES.index(a.to)
    if to >= cur:
        raise GateError("reopen chỉ dùng để LÙI stage (ví dụ ANALYZED -> HARVESTED để phân tích lại)")
    if a.to in ("CREATED", "READY") and p.stage in ("RUNNING", "COMPLETE", "HARVESTED", "ANALYZED", "VALIDATED", "DELIVERED"):
        ui.warn("Lùi về trước RUNNING nghĩa là sẽ chạy mô phỏng lại; kết quả cũ trên Drive vẫn còn và job đã có CSV sẽ bị bỏ qua.")
    p.set_stage(a.to, f"REOPEN: {a.reason}")


def cmd_show(a):
    p = _p(a.project)
    print(json.dumps({"name": p.name, "stage": p.stage, "repo": p.repo, "history": p.state()["history"][-10:]},
                     indent=1, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m goat", description="GOAT - vận hành cụm mô phỏng hạt nhân")
    sp = ap.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("doctor", help="Kiểm tra máy/tài khoản/thư mục gốc")
    s.add_argument("--offline", action="store_true", help="Bỏ qua kiểm tra token/Drive qua mạng")
    s.set_defaults(fn=cmd_doctor)
    sp.add_parser("accounts", help="Kiểm tra token các tài khoản ACTIVE").set_defaults(fn=cmd_accounts)
    s = sp.add_parser("auth-drive", help="Đăng nhập Google qua trình duyệt, tạo secrets/gdrive_oauth.json")
    s.add_argument("--client-secret", help="File OAuth client (Desktop app) tải từ Google Cloud Console")
    s.add_argument("--force", action="store_true", help="Thay tài khoản Drive đang dùng (file cũ giữ lại .old)")
    s.set_defaults(fn=cmd_auth_drive)
    s = sp.add_parser("export", help="Đóng gói hệ thống (không secrets/project/.venv) để mang sang máy khác")
    s.add_argument("--out", help="File .zip hoặc thư mục đích (mặc định tmp/)")
    s.add_argument("--with-oauth-client", action="store_true",
                   help="Kèm OAuth client (không kèm token) để máy mới chạy auth-drive không cần Google Cloud Console")
    s.set_defaults(fn=cmd_export)
    s = sp.add_parser("new", help="Tạo project mới")
    s.add_argument("project", help="Tên dự án, ví dụ Paper9")
    s.add_argument("variant", help="Điểm đặc biệt/phiên bản, ví dụ MAC-v1")
    s.add_argument("tool", help="Geant4 | FLUKA | PHITS | OpenMC")
    s.add_argument("--date", help="YYYY.MM.DD (mặc định hôm nay)")
    s.set_defaults(fn=cmd_new)
    sp.add_parser("list", help="Liệt kê project và stage").set_defaults(fn=cmd_list)
    s = sp.add_parser("detect", help="Nhận diện thư mục code/input (công cụ, mẫu, bề dày, năng lượng); --into để nạp vào project")
    s.add_argument("folder", help="Thư mục code/input người dùng đưa")
    s.add_argument("--into", help="Tên project (stage CREATED) để nạp vào sim/<sub>/ theo chuẩn")
    s.add_argument("--sub", default="MAIN", help="Tên nhóm bài toán (subproject), vd MAC, MSP, R")
    s.add_argument("--thickness-unit", choices=["mm", "cm"], help="Geant4: đơn vị bề dày trong macro khi không ghi (mặc định mm)")
    s.set_defaults(fn=cmd_detect)
    s = sp.add_parser("clean", help="Cách ly file rác vào tmp/_quarantine, xoá tmp quá hạn")
    s.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê, không di chuyển")
    s.set_defaults(fn=cmd_clean)

    for name, fn, hlp in (
        ("check", cmd_check, "Kiểm tra cấu hình + input (không đổi stage)"),
        ("show", cmd_show, "Xem stage và lịch sử"),
        ("prepare", cmd_prepare, "CREATED->READY: manifest, plan, cây Drive"),
        ("analyze", cmd_analyze, "HARVESTED->ANALYZED: tính đại lượng, xuất Excel"),
        ("validate", cmd_validate, "ANALYZED->VALIDATED: kiểm định V1-V4"),
        ("rescue", cmd_rescue, "Cứu hộ thủ công một vòng"),
    ):
        s = sp.add_parser(name, help=hlp)
        s.add_argument("project")
        s.set_defaults(fn=fn)

    s = sp.add_parser("harvest", help="COMPLETE->HARVESTED: gom kết quả, gộp Raw_Merged, benchmark")
    s.add_argument("project")
    s.add_argument("--keep-on-drive", action="store_true",
                   help="Không lưu file lẻ/output xuống máy (chỉ Raw_Merged + benchmark); tải sau bằng goat fetch")
    s.set_defaults(fn=cmd_harvest)
    s = sp.add_parser("deliver", help="VALIDATED->DELIVERED: đẩy kết quả lên Drive")
    s.add_argument("project")
    s.add_argument("--purge-local", action="store_true",
                   help="Bàn giao xong thì xoá results/ ở máy (dữ liệu chỉ còn trên Drive; tải lại bằng goat fetch)")
    s.set_defaults(fn=cmd_deliver)
    s = sp.add_parser("fetch", help="Tải thêm về máy phần đang chỉ có trên Drive (bất kỳ lúc nào sau COMPLETE)")
    s.add_argument("project")
    s.add_argument("--what", nargs="+", default=["all"], choices=["csv", "outputs", "inputs", "logs", "runners", "all"],
                   help="Phần cần tải (mặc định all)")
    s.set_defaults(fn=cmd_fetch)
    s = sp.add_parser("deploy", help="READY->RUNNING: push + dispatch")
    s.add_argument("project")
    s.add_argument("--allow-concurrent", action="store_true")
    s.set_defaults(fn=cmd_deploy)
    s = sp.add_parser("status", help="Tiến độ hiện tại")
    s.add_argument("project")
    s.add_argument("--missing", action="store_true", help="In danh sách job thiếu")
    s.set_defaults(fn=cmd_status)
    s = sp.add_parser("watch", help="Giám sát + tự cứu hộ tới khi đủ 100%%")
    s.add_argument("project")
    s.add_argument("--interval", type=int, default=300)
    s.add_argument("--max-rounds", type=int, default=None)
    s.set_defaults(fn=cmd_watch)
    s = sp.add_parser("cancel", help="Hủy mọi run đang chạy, về READY")
    s.add_argument("project")
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_cancel)
    s = sp.add_parser("archive", help="DELIVERED->ARCHIVED, đánh dấu đã xong, xoá .deploy/")
    s.add_argument("project")
    s.add_argument("--reason", default="")
    s.set_defaults(fn=cmd_archive)
    s = sp.add_parser("reopen", help="Lùi stage (phải ghi lý do)")
    s.add_argument("project")
    s.add_argument("--to", required=True, choices=project.STAGES)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_reopen)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (GateError, ValueError, FileNotFoundError) as e:
        ui.result_fail(str(e))
        return 1
    except KeyboardInterrupt:
        ui.result_fail("người dùng dừng")
        return 1
    ui.result_ok(a.cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())

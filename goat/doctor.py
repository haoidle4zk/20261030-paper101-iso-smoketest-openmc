"""DOCTOR: kiểm tra máy mới / tài khoản mới có đủ điều kiện vận hành hay không, và giữ thư mục gốc gọn."""

import importlib
import shutil
import sys

from . import creds, drive, github, paths, ui

REQUIRED_PKGS = ["yaml", "pandas", "openpyxl", "requests", "nacl", "googleapiclient", "google.oauth2"]
def tidy_issues() -> list:
    from . import clean
    return [f"{p.relative_to(paths.ROOT)} ({why})" for p, why in clean.scan_root()]


def run(online: bool = True) -> bool:
    bad = 0
    ui.step("Python & thư viện")
    if sys.version_info < (3, 10):
        ui.fail(f"Cần Python >= 3.10, đang có {sys.version.split()[0]}")
        bad += 1
    else:
        ui.ok(f"Python {sys.version.split()[0]}")
    for m in REQUIRED_PKGS:
        try:
            importlib.import_module(m)
            ui.ok(m)
        except ImportError:
            ui.fail(f"thiếu {m} -> chạy setup.ps1 (Windows) hoặc setup.sh (Linux/macOS)")
            bad += 1
    if sys.platform == "win32" and len(str(paths.ROOT)) > 60:
        ui.warn(f"Đường dẫn gốc dài {len(str(paths.ROOT))} ký tự; Windows giới hạn 260 ký tự/đường dẫn. "
                "Nên đặt hệ thống ở thư mục ngắn, vd C:\\goat")
    if shutil.which("git"):
        ui.ok("git")
    else:
        ui.fail("thiếu git")
        bad += 1

    ui.step("Bí mật (secrets/)")
    accs = {}
    try:
        accs = creds.accounts(active_only=False)
        act = [a for a, v in accs.items() if v.get("status") == "ACTIVE"]
        ui.ok(f"accounts.json: {len(accs)} tài khoản, {len(act)} ACTIVE")
    except Exception as e:  # noqa: BLE001
        ui.fail(str(e))
        bad += 1
    try:
        creds.gdrive_oauth_str()
        ui.ok("gdrive_oauth.json có mặt")
    except Exception as e:  # noqa: BLE001
        ui.fail(str(e))
        bad += 1

    if online and accs:
        ui.step("Kiểm tra token GitHub (online)")
        for name, info in accs.items():
            if info.get("status") != "ACTIVE":
                continue
            ok, msg = github.check_account(name, info.get("token", ""))
            (ui.ok if ok else ui.fail)(f"{name:<18} {msg}")
            bad += 0 if ok else 1
        ui.step("Kiểm tra Google Drive (online)")
        try:
            about = drive.service().about().get(fields="user(emailAddress)").execute()
            ui.ok(f"Drive đăng nhập: {about['user']['emailAddress']}")
        except Exception as e:  # noqa: BLE001
            ui.fail(f"Drive lỗi: {str(e)[:200]}")
            bad += 1

    ui.step("Thư mục gốc gọn gàng")
    issues = tidy_issues()
    if issues:
        for i in issues[:30]:
            ui.warn(f"rác: {i}")
        ui.warn(f"{len(issues)} mục rác -> chạy: python -m goat clean")
    else:
        ui.ok("không có file/thư mục lạ")
    return bad == 0

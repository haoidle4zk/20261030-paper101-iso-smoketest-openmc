"""Mang hệ thống sang máy khác / tài khoản khác.

auth-drive : đăng nhập Google qua trình duyệt -> secrets/gdrive_oauth.json (Drive nào cũng được)
export     : đóng gói hệ thống sạch thành 1 file zip (không secrets, không project, không .venv)
"""

import json
import os
import time
import zipfile
from pathlib import Path

from . import paths, ui
from .ui import GateError

SCOPES = ["https://www.googleapis.com/auth/drive"]
OAUTH_CLIENT_FILE = paths.SECRETS / "oauth_client.json"   # chỉ client_id + client_secret (không có token)


def _client_config(client_secret: str = None) -> dict:
    """Ưu tiên: file --client-secret (tải từ Google Cloud Console) > secrets/client_secret.json >
    secrets/oauth_client.json > client lấy từ gdrive_oauth.json đang có."""
    for cand in [client_secret, paths.SECRETS / "client_secret.json"]:
        if cand and Path(cand).exists():
            data = json.loads(Path(cand).read_text(encoding="utf-8"))
            if "installed" in data or "web" in data:
                return data
            raise GateError(f"{cand} không phải file OAuth client (cần khoá 'installed' — loại Desktop app)")
    src = None
    if OAUTH_CLIENT_FILE.exists():
        src = json.loads(OAUTH_CLIENT_FILE.read_text(encoding="utf-8"))
    elif paths.GDRIVE_OAUTH_FILE.exists():
        src = json.loads(paths.GDRIVE_OAUTH_FILE.read_text(encoding="utf-8"))
    if not src or not src.get("client_id"):
        raise GateError("Chưa có OAuth client. Tạo trên Google Cloud Console (loại Desktop app), tải JSON về rồi chạy:\n"
                        "  python -m goat auth-drive --client-secret <đường dẫn file client_secret_xxx.json>\n"
                        "Hướng dẫn chi tiết: docs/runbooks/10_tai_khoan_bi_mat.md mục C")
    return {"installed": {"client_id": src["client_id"], "client_secret": src["client_secret"],
                          "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                          "token_uri": src.get("token_uri", "https://oauth2.googleapis.com/token"),
                          "redirect_uris": ["http://localhost"]}}


def auth_drive(client_secret: str = None, force: bool = False):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from . import drive

    if paths.GDRIVE_OAUTH_FILE.exists() and not force:
        raise GateError(f"{paths.GDRIVE_OAUTH_FILE.relative_to(paths.ROOT)} đã tồn tại. "
                        "Muốn đổi sang tài khoản Google khác: thêm --force (file cũ được giữ lại dạng .old)")
    cfg = _client_config(client_secret)
    ui.step("Mở trình duyệt để đăng nhập Google (chọn tài khoản Drive sẽ chứa kết quả, bấm Cho phép)")
    ui.info("Nếu trình duyệt không tự mở: copy đường link in ra bên dưới vào trình duyệt.")
    flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        raise GateError("Google không trả refresh_token. Vào https://myaccount.google.com/permissions gỡ quyền app rồi chạy lại.")

    paths.SECRETS.mkdir(exist_ok=True)
    if paths.GDRIVE_OAUTH_FILE.exists():
        old = paths.GDRIVE_OAUTH_FILE.with_suffix(f".old_{time.strftime('%Y%m%d_%H%M%S')}.json")
        paths.GDRIVE_OAUTH_FILE.replace(old)
        ui.info(f"file cũ -> {old.relative_to(paths.ROOT)}")
    paths.GDRIVE_OAUTH_FILE.write_text(creds.to_json(), encoding="utf-8")
    inst = cfg.get("installed") or cfg.get("web")
    OAUTH_CLIENT_FILE.write_text(json.dumps({"client_id": inst["client_id"], "client_secret": inst["client_secret"],
                                             "token_uri": inst.get("token_uri")}, indent=1), encoding="utf-8")
    email = drive.service().about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]
    ui.ok(f"Đã lưu {paths.GDRIVE_OAUTH_FILE.relative_to(paths.ROOT)} — Drive đăng nhập: {email}")
    ui.info("Project mới sẽ vào My Drive/GOAT_Projects/ của tài khoản này. Project đang RUNNING không được đổi Drive.")


EXPORT_DIRS = ["goat", "docs", "templates", "binaries", ".agents"]
EXPORT_FILES = ["HUONG_DAN_CAI_DAT.txt", "VI_DU_PROMPT.txt", "GEMINI.md", "AGENTS.md", "README.md", "requirements.txt", "setup.ps1", "setup.sh", ".gitignore"]
STORE_SUFFIXES = {".gz", ".zip", ".h5", ".deb"}


def export(out: str = None, with_oauth_client: bool = False) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M")
    dest = Path(out) if out else paths.ROOT / "tmp" / f"goat_portable_{stamp}.zip"
    if dest.suffix != ".zip":
        dest = dest / f"goat_portable_{stamp}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for d in EXPORT_DIRS:
            for f in sorted((paths.ROOT / d).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts and not f.name.endswith(".pyc"):
                    is_part = ".part_" in f.name
                    z.write(f, f.relative_to(paths.ROOT),
                            compress_type=zipfile.ZIP_STORED if f.suffix in STORE_SUFFIXES or is_part else zipfile.ZIP_DEFLATED)
                    n += 1
        for name in EXPORT_FILES:
            if (paths.ROOT / name).exists():
                z.write(paths.ROOT / name, name)
                n += 1
        for d in ("projects", "registry", "tmp"):
            z.writestr(f"{d}/", "")
        # project ví dụ (templates/examples/<tên>) đặt sẵn vào projects/ để máy mới chạy thử ngay
        for ex in sorted((paths.TEMPLATES / "examples").glob("*")) if (paths.TEMPLATES / "examples").is_dir() else []:
            for f in sorted(ex.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, Path("projects") / f.relative_to(paths.TEMPLATES / "examples"))
            ui.info(f"kèm project ví dụ: projects/{ex.name}")
        for f in ("README.md", "accounts.example.json"):
            if (paths.SECRETS / f).exists():
                z.write(paths.SECRETS / f, f"secrets/{f}")
        if with_oauth_client:
            if not OAUTH_CLIENT_FILE.exists() and not paths.GDRIVE_OAUTH_FILE.exists():
                raise GateError("Không có OAuth client để kèm theo")
            src = json.loads((OAUTH_CLIENT_FILE if OAUTH_CLIENT_FILE.exists() else paths.GDRIVE_OAUTH_FILE).read_text(encoding="utf-8"))
            z.writestr("secrets/oauth_client.json", json.dumps({k: src.get(k) for k in ("client_id", "client_secret", "token_uri")}, indent=1))
            ui.warn("Đã kèm secrets/oauth_client.json (chỉ client, KHÔNG có token đăng nhập). Không chia sẻ file zip này công khai.")
    leaked = [i for i in zipfile.ZipFile(dest).namelist()
              if i.endswith(("accounts.json", "gdrive_oauth.json")) and "example" not in i]
    if leaked:
        dest.unlink()
        raise GateError(f"Gói chứa bí mật {leaked} — đã huỷ file zip")
    ui.ok(f"{n} file -> {dest} ({dest.stat().st_size / 1e6:.0f} MB)")
    ui.info("Máy mới: giải nén rồi làm theo file HUONG_DAN_CAI_DAT.txt ở thư mục gốc")
    return dest

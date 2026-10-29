"""Nạp bí mật. Thứ tự ưu tiên: biến môi trường -> secrets/.

- GOAT_ACCOUNTS_JSON  hoặc secrets/accounts.json  : tài khoản GitHub + PAT token
- GDRIVE_OAUTH_JSON   hoặc secrets/gdrive_oauth.json : OAuth Google Drive (authorized user)

KHÔNG BAO GIỜ in nội dung token ra màn hình, không copy file secrets vào project, không commit.
"""

import json
import os

from . import paths
from .ui import GateError


def accounts(active_only: bool = True) -> dict:
    raw = os.environ.get("GOAT_ACCOUNTS_JSON")
    if raw:
        data = json.loads(raw)
    elif paths.ACCOUNTS_FILE.exists():
        with open(paths.ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        raise GateError(f"Thiếu {paths.ACCOUNTS_FILE} (xem secrets/README.md)")
    accs = data.get("accounts", {})
    if active_only:
        accs = {k: v for k, v in accs.items() if v.get("status") == "ACTIVE"}
    return accs


def gdrive_oauth_str() -> str:
    raw = os.environ.get("GDRIVE_OAUTH_JSON")
    if raw:
        return raw
    if paths.GDRIVE_OAUTH_FILE.exists():
        return paths.GDRIVE_OAUTH_FILE.read_text(encoding="utf-8")
    raise GateError(f"Thiếu {paths.GDRIVE_OAUTH_FILE} (xem secrets/README.md)")


def mask(token: str) -> str:
    return (token[:4] + "…" + token[-2:]) if token and len(token) > 8 else "***"

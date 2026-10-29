"""GitHub API: kiểm tra tài khoản, tạo repo, nạp secret, push, dispatch, đọc trạng thái run."""

import subprocess
import time
from base64 import b64encode
from pathlib import Path

import requests

API = "https://api.github.com"


def _h(token: str) -> dict:
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}


def _req(method: str, url: str, token: str, **kw):
    for i in range(4):
        try:
            r = requests.request(method, url, headers=_h(token), timeout=20, **kw)
            if r.status_code in (502, 503, 504):
                raise requests.ConnectionError(f"HTTP {r.status_code}")
            return r
        except requests.RequestException:
            if i == 3:
                raise
            time.sleep(2 * (i + 1))


def check_account(name: str, token: str) -> tuple:
    """(ok, message). Kiểm tra token hợp lệ và đúng chủ tài khoản."""
    r = _req("GET", f"{API}/user", token)
    if r.status_code != 200:
        return False, f"token lỗi HTTP {r.status_code}"
    login = r.json().get("login", "")
    if login.lower() != name.lower():
        return False, f"token thuộc '{login}', không phải '{name}'"
    scopes = r.headers.get("X-OAuth-Scopes", "")
    need = {"repo", "workflow"}
    if scopes and not need.issubset({s.strip() for s in scopes.split(",")}):
        return False, f"token thiếu quyền repo/workflow (có: {scopes})"
    return True, "OK"


def ensure_repo(owner: str, token: str, repo: str) -> bool:
    r = _req("GET", f"{API}/repos/{owner}/{repo}", token)
    if r.status_code == 200:
        return True
    r = _req("POST", f"{API}/user/repos", token, json={"name": repo, "private": False, "auto_init": False})
    return r.status_code in (201, 422)


def set_secret(owner: str, token: str, repo: str, name: str, value: str) -> bool:
    import nacl.encoding
    import nacl.public

    r = _req("GET", f"{API}/repos/{owner}/{repo}/actions/secrets/public-key", token)
    if r.status_code != 200:
        return False
    k = r.json()
    box = nacl.public.SealedBox(nacl.public.PublicKey(k["key"].encode(), nacl.encoding.Base64Encoder))
    enc = b64encode(box.encrypt(value.encode())).decode()
    r = _req("PUT", f"{API}/repos/{owner}/{repo}/actions/secrets/{name}", token,
             json={"encrypted_value": enc, "key_id": k["key_id"]})
    return r.status_code in (201, 204)


def _git(bundle: Path, *args):
    return subprocess.run(["git", *args], cwd=str(bundle), capture_output=True, text=True)


def init_bundle(bundle: Path):
    """Commit bundle một lần (gọi trước khi push song song nhiều tài khoản)."""
    if not (bundle / ".git").exists():
        _git(bundle, "init", "-q")
        _git(bundle, "config", "user.name", "goat-deployer")
        _git(bundle, "config", "user.email", "goat@localhost")
        _git(bundle, "add", "-A")
        _git(bundle, "commit", "-q", "-m", "goat deploy")
        _git(bundle, "branch", "-M", "main")


def push_bundle(bundle: Path, owner: str, token: str, repo: str) -> tuple:
    """Force-push thư mục bundle lên nhánh main của owner/repo. Token không bao giờ được in ra."""
    def git(*args):
        return _git(bundle, *args)

    init_bundle(bundle)
    url = f"https://{owner}:{token}@github.com/{owner}/{repo}.git"
    res = git("push", "-q", "--force", url, "main")
    err = (res.stderr or "").replace(token, "***")
    return res.returncode == 0, err.strip()[-300:]


def dispatch(owner: str, token: str, repo: str, workflow: str, inputs: dict) -> tuple:
    r = _req("POST", f"{API}/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches", token,
             json={"ref": "main", "inputs": inputs})
    return r.status_code == 204, (r.text or "")[:200]


def list_runs(owner: str, token: str, repo: str, per_page: int = 30) -> list:
    r = _req("GET", f"{API}/repos/{owner}/{repo}/actions/runs?per_page={per_page}", token)
    if r.status_code != 200:
        return []
    return r.json().get("workflow_runs", [])


def list_jobs(owner: str, token: str, repo: str, run_id: int) -> list:
    r = _req("GET", f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100", token)
    if r.status_code != 200:
        return []
    return r.json().get("jobs", [])


def cancel_run(owner: str, token: str, repo: str, run_id: int) -> bool:
    r = _req("POST", f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}/cancel", token)
    return r.status_code == 202

"""Google Drive: một module duy nhất cho cả máy trạm và runner.

Cấu trúc bắt buộc trên Drive (giống hệt results/ ở máy trạm):
  <parent_folder_id | My Drive/GOAT_Projects>/<TÊN PROJECT>/
     01_Summary_Excel_Results/<sub>/*.xlsx
     02_Flux_Data/<sub>/<SAMPLE>/<job>.csv          (1 job = 1 CSV)
     02_Flux_Data/<sub>/Raw_Merged_<sub>.csv
     03_Simulation_Outputs/<sub>/<job>/...           (KHÔNG zip)
     04_Input_Decks/<sub>/...                        (file input gốc)
     05_Execution_Logs_and_Benchmarks/<sub>/<job>.log, benchmark, status
"""

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import paths

FOLDER_MIME = "application/vnd.google-apps.folder"
DEFAULT_PARENT_NAME = "GOAT_Projects"  # thư mục chung trong My Drive khi project không chỉ định parent_folder_id
_RETRY_MARKERS = ("ratelimitexceeded", "userratelimit", "403", "429", "500", "502", "503", "quota", "timed out", "connection")


RETRY_TRIES = 7  # worker trên runner nâng lên để chịu được quota Drive khi hàng trăm runner cùng ghi


def retry(fn, tries: int = None, base: float = 2.0):
    tries = tries or RETRY_TRIES
    for i in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if i == tries or not any(m in msg for m in _RETRY_MARKERS):
                raise
            time.sleep(base * (2 ** (i - 1)) + random.uniform(0.5, 3.0))


def service(oauth_str: str = None):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from . import creds

    info = json.loads(oauth_str or creds.gdrive_oauth_str())
    c = Credentials.from_authorized_user_info(info)
    return build("drive", "v3", credentials=c, cache_discovery=False)


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def list_children(svc, parent_id: str, folders_only: bool = False, name: str = None) -> list:
    q = f"'{parent_id}' in parents and trashed = false"
    if folders_only:
        q += f" and mimeType = '{FOLDER_MIME}'"
    if name:
        q += f" and name = '{_q(name)}'"
    out, token = [], None
    while True:
        res = retry(lambda: svc.files().list(
            q=q, pageSize=1000, pageToken=token, fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute())
        out.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            return out


def ensure_folder(svc, parent_id: str, name: str) -> str:
    found = list_children(svc, parent_id, folders_only=True, name=name)
    if found:
        return found[0]["id"]
    meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    return retry(lambda: svc.files().create(body=meta, fields="id", supportsAllDrives=True).execute())["id"]


def upload(svc, local: Path, folder_id: str, name: str = None) -> str:
    """Tạo mới hoặc ghi đè file cùng tên trong folder. Trả về file id."""
    from googleapiclient.http import MediaFileUpload

    local = Path(local)
    name = name or local.name
    existing = list_children(svc, folder_id, name=name)
    media = MediaFileUpload(str(local), resumable=True)
    if existing:
        fid = existing[0]["id"]
        retry(lambda: svc.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute())
        return fid
    meta = {"name": name, "parents": [folder_id]}
    return retry(lambda: svc.files().create(body=meta, media_body=media, fields="id", supportsAllDrives=True).execute())["id"]


SIMPLE_UPLOAD_MAX = 5 * 1024 * 1024


def _media(local: Path):
    from googleapiclient.http import MediaFileUpload
    small = local.stat().st_size <= SIMPLE_UPLOAD_MAX
    # file nhỏ: 1 request (multipart); file lớn: resumable theo chunk 10MB
    return MediaFileUpload(str(local), resumable=not small, chunksize=CHUNK if not small else -1)


def create_or_update(svc, local: Path, folder_id: str, existing_id: str = None) -> str:
    """Ghi file KHÔNG cần hỏi Drive trước (người gọi đã biết file có sẵn hay chưa). Trả về file id."""
    local = Path(local)
    if existing_id:
        retry(lambda: svc.files().update(fileId=existing_id, media_body=_media(local), supportsAllDrives=True).execute())
        return existing_id
    meta = {"name": local.name, "parents": [folder_id]}
    return retry(lambda: svc.files().create(body=meta, media_body=_media(local), fields="id", supportsAllDrives=True).execute())["id"]


def create_folder(svc, parent_id: str, name: str) -> str:
    meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    return retry(lambda: svc.files().create(body=meta, fields="id", supportsAllDrives=True).execute())["id"]


def upload_dir(svc, local_dir: Path, folder_id: str):
    for p in sorted(Path(local_dir).iterdir()):
        if p.is_dir():
            upload_dir(svc, p, ensure_folder(svc, folder_id, p.name))
        else:
            upload(svc, p, folder_id)


CHUNK = 10 * 1024 * 1024


def download(svc, file_id: str, dest: Path):
    """Tải nguyên tử: ghi vào <tên>.part, đủ 100% mới đổi tên. Lỗi giữa chừng không để lại file hỏng."""
    from googleapiclient.http import MediaIoBaseDownload

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    def _once():
        try:
            with open(part, "wb") as fh:
                dl = MediaIoBaseDownload(fh, svc.files().get_media(fileId=file_id, supportsAllDrives=True), chunksize=CHUNK)
                done = False
                while not done:
                    _, done = dl.next_chunk()
            part.replace(dest)
        finally:
            if part.exists():
                part.unlink()

    retry(_once, tries=5)


# ---------------------------------------------------------------------------
# Song song: mỗi luồng có service riêng (client Google API không an toàn khi dùng chung giữa các luồng)
# ---------------------------------------------------------------------------
_local = threading.local()


def thread_service():
    if not hasattr(_local, "svc"):
        _local.svc = service()
    return _local.svc


def list_many(folder_ids: list, workers: int = 16) -> dict:
    """{folder_id: [file...]} — quét nhiều thư mục cùng lúc."""
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = dict(zip(folder_ids, ex.map(lambda f: list_children(thread_service(), f), folder_ids)))
    return res


def download_many(items: list, workers: int = 16, label: str = "tải") -> tuple:
    """items = [(file_id, dest Path, size_on_drive)]. Bỏ qua file đã có cùng kích thước.
    Trả về (số tải mới, số bỏ qua, [lỗi])."""
    todo = [(fid, Path(d)) for fid, d, size in items
            if not (Path(d).exists() and size is not None and str(Path(d).stat().st_size) == str(size))]
    skipped, errors, done = len(items) - len(todo), [], 0
    if not todo:
        return 0, skipped, errors
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(lambda a: download(thread_service(), a[0], a[1]), it): it for it in todo}
        for fut in as_completed(futs):
            try:
                fut.result()
                done += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{futs[fut][1].name}: {str(e)[:150]}")
            if done and done % 250 == 0:
                print(f"         {label}: {done}/{len(todo)} ({done / (time.time() - t0):.0f} file/s)")
    return done, skipped, errors


def upload_many(files: list, folder_id: str, workers: int = 8) -> list:
    """Upload nhiều file vào CÙNG một thư mục: quét thư mục một lần, rồi tạo mới/ghi đè song song. Trả về [lỗi]."""
    existing = {f["name"]: f["id"] for f in list_children(thread_service(), folder_id)}

    def _one(local: Path):
        create_or_update(thread_service(), local, folder_id, existing.get(local.name))

    errors = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, Path(f)): f for f in files}
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{Path(futs[fut]).name}: {str(e)[:150]}")
    return errors


def init_project_tree(svc, project) -> dict:
    """Tạo (hoặc tìm lại) toàn bộ cây thư mục của project trên Drive. Idempotent."""
    parent = (project.cfg.get("drive") or {}).get("parent_folder_id") or ensure_folder(svc, "root", DEFAULT_PARENT_NAME)
    root = project.cfg["drive"].get("root_folder_id") or ensure_folder(svc, parent, project.name)
    tree = {"project": project.name, "root": root, "tiers": {}, "sub": {}, "flux_samples": {}}
    for t in paths.TIERS:
        tree["tiers"][t] = ensure_folder(svc, root, t)
    for sub in project.subprojects:
        n = sub["name"]
        tree["sub"][n] = {t: ensure_folder(svc, tree["tiers"][t], n) for t in paths.TIERS}
        samples = {s.upper() for s in sub.get("samples", [])}
        if sub.get("blank"):
            samples.add(sub["blank"]["sample"].upper())
        if sub.get("jobs_file"):
            import csv
            with open(project.sim / n / sub["jobs_file"], "r", encoding="utf-8") as f:
                samples |= {r["sample"].upper() for r in csv.DictReader(f)}
        tree["flux_samples"][n] = {s: ensure_folder(svc, tree["sub"][n]["02_Flux_Data"], s) for s in sorted(samples)}
    return tree


def read_many(file_ids: list, workers: int = 16) -> dict:
    """{file_id: bytes} — đọc nội dung nhiều file nhỏ thẳng vào bộ nhớ, không ghi ra đĩa."""
    def _get(fid):
        return fid, retry(lambda: thread_service().files().get_media(fileId=fid, supportsAllDrives=True).execute())
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (fid, data) in enumerate(ex.map(_get, file_ids), 1):
            out[fid] = data
            if i % 500 == 0:
                print(f"         đọc: {i}/{len(file_ids)}")
    return out


def walk_tree(folder_id: str, rel: Path = Path(".")) -> list:
    """Liệt kê đệ quy (song song theo từng tầng) mọi file dưới folder_id -> [(file_id, đường dẫn tương đối, size)]."""
    files, level = [], [(folder_id, Path(rel))]
    while level:
        listing = list_many([fid for fid, _ in level])
        nxt = []
        for fid, base in level:
            for f in listing[fid]:
                if f.get("mimeType") == FOLDER_MIME:
                    nxt.append((f["id"], base / f["name"]))
                else:
                    files.append((f["id"], base / f["name"], f.get("size")))
        level = nxt
    return files

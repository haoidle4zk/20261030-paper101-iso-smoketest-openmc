"""Quy ước đặt tên DUY NHẤT của hệ thống. Không được đặt tên bằng tay theo kiểu khác.

1. Tên project (= tên thư mục trong projects/ = tên thư mục gốc trên Drive):
       YYYY.MM.DD_<DuAn>_<DiemDacBiet>_<CongCu>
   - YYYY.MM.DD : ngày tạo, có số 0 đứng trước (2026.10.07, không phải 2026.10.7)
   - <DuAn>     : chữ/số/gạch ngang/dấu chấm, ví dụ Paper2, Paper10.1, KaiAlloy
   - <DiemDacBiet>: phiên bản/biến thể, chữ/số/gạch ngang/dấu chấm, ví dụ v1, 1e6, MAC-3cm, MAC-uncollided-v2
   - <CongCu>   : đúng một trong Geant4 | FLUKA | PHITS | OpenMC
   Ví dụ: 2026.10.22_Paper2_MAC-3cm_Geant4, 2026.10.29_Paper10.1_1e6_PHITS (10^6 viết 1e6)

2. Tên job (= tên file CSV kết quả, tên thư mục output, tên log):
       <SAMPLE>_<bề dày>cm_<năng lượng>keV
   - Dấu thập phân viết bằng chữ p:  0.5 -> 0p5,  59.5 -> 59p5,  0.005 -> 0p005
   - SAMPLE viết HOA: S1, S2, BLANK
   Ví dụ: S1_0p5cm_59p5keV ;  BLANK_0p5cm_662keV
"""

import re
from datetime import date

TOOLS = ["Geant4", "FLUKA", "PHITS", "OpenMC"]

_TOKEN = r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
PROJECT_RE = re.compile(
    rf"^(?P<date>\d{{4}}\.\d{{2}}\.\d{{2}})_(?P<project>{_TOKEN})_(?P<variant>{_TOKEN})_(?P<tool>{'|'.join(TOOLS)})$"
)
JOB_RE = re.compile(r"^(?P<sample>[A-Z0-9-]+)_(?P<thick>[0-9p]+)cm_(?P<energy>[0-9p]+)keV$")


def fmt_num(x: float) -> str:
    """0.5 -> '0p5', 59.5 -> '59p5', 662.0 -> '662', 0.005 -> '0p005'."""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s.replace(".", "p")


def parse_num(s: str) -> float:
    return float(s.replace("p", "."))


def job_name(sample: str, thickness_cm: float, energy_kev: float) -> str:
    return f"{str(sample).upper().strip()}_{fmt_num(thickness_cm)}cm_{fmt_num(energy_kev)}keV"


def parse_job_name(name: str) -> dict:
    m = JOB_RE.match(name)
    if not m:
        raise ValueError(f"Tên job sai chuẩn: {name}")
    return {
        "sample": m.group("sample"),
        "thickness_cm": parse_num(m.group("thick")),
        "energy_kev": parse_num(m.group("energy")),
    }


def check_project_name(name: str) -> dict:
    m = PROJECT_RE.match(name)
    if not m:
        raise ValueError(
            f"Tên project sai chuẩn: '{name}'.\n"
            "  Đúng mẫu: YYYY.MM.DD_<DuAn>_<DiemDacBiet>_<CongCu>\n"
            f"  CongCu thuộc: {', '.join(TOOLS)}\n"
            "  Mỗi phần chỉ dùng chữ, số, gạch ngang (-), dấu chấm (.) (không đứng đầu/cuối); "
            "phân cách các phần bằng gạch dưới (_).\n"
            "  Không dùng ^, dấu cách hay ký tự đặc biệt: 10^6 viết 1e6.\n"
            "  Ví dụ: 2026.10.22_Paper2_MAC-3cm_Geant4, 2026.10.29_Paper10.1_1e6_PHITS"
        )
    y, mo, d = (int(v) for v in m.group("date").split("."))
    date(y, mo, d)  # ném lỗi nếu ngày không tồn tại
    return m.groupdict()


def make_project_name(project: str, variant: str, tool: str, on: date = None) -> str:
    on = on or date.today()
    name = f"{on:%Y.%m.%d}_{project}_{variant}_{tool}"
    check_project_name(name)
    return name


def repo_name(project_name: str) -> str:
    """Tên repo GitHub sinh tự động từ tên project: chữ thường, chỉ a-z0-9-."""
    s = project_name.lower().replace(".", "").replace("_", "-")
    return re.sub(r"[^a-z0-9-]", "", s)

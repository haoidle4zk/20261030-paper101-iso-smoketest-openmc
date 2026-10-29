"""Mọi đường dẫn của hệ thống được tính tương đối từ thư mục gốc.

KHÔNG BAO GIỜ viết đường dẫn tuyệt đối (C:\\...) trong code hay project.
Thư mục gốc = thư mục chứa package goat/, hoặc biến môi trường GOAT_HOME nếu được đặt.
"""

import os
from pathlib import Path

ROOT = Path(os.environ.get("GOAT_HOME") or Path(__file__).resolve().parent.parent)

GOAT_PKG = ROOT / "goat"
PROJECTS = ROOT / "projects"
SECRETS = ROOT / "secrets"
REGISTRY = ROOT / "registry" / "projects.json"
TEMPLATES = ROOT / "templates"
BINARIES = ROOT / "binaries"
DOCS = ROOT / "docs"

ACCOUNTS_FILE = SECRETS / "accounts.json"
GDRIVE_OAUTH_FILE = SECRETS / "gdrive_oauth.json"

# Cấu trúc bên trong một project
SIM_DIR = "sim"
RESULTS_DIR = "results"
ANALYSIS_DIR = "analysis"
THEORY_DIR = "theory"
LOGS_DIR = "logs"
DEPLOY_DIR = ".deploy"
PROJECT_FILE = "project.yaml"
MANIFEST_FILE = "manifest.csv"
PLAN_FILE = "plan.json"
STATE_FILE = "state.json"
DRIVE_FOLDERS_FILE = "drive_folders.json"

# 5 tầng Google Drive / results/ (bắt buộc, đúng thứ tự, đúng tên)
TIERS = [
    "01_Summary_Excel_Results",
    "02_Flux_Data",
    "03_Simulation_Outputs",
    "04_Input_Decks",
    "05_Execution_Logs_and_Benchmarks",
]


def project_dir(name: str) -> Path:
    return PROJECTS / name

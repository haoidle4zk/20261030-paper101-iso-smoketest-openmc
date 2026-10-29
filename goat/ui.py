"""In thông báo thống nhất. Mọi lệnh kết thúc bằng đúng một dòng KẾT QUẢ: OK / FAIL."""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class GateError(Exception):
    """Điều kiện cổng (gate) không đạt. Lệnh phải dừng, không được tự sửa vòng qua."""


def step(msg: str):
    print(f"\n>>> {msg}")


def ok(msg: str):
    print(f"  [OK]   {msg}")


def warn(msg: str):
    print(f"  [WARN] {msg}")


def fail(msg: str):
    print(f"  [FAIL] {msg}")


def info(msg: str):
    print(f"         {msg}")


def result_ok(msg: str):
    print(f"\nKẾT QUẢ: OK - {msg}")


def result_fail(msg: str):
    print(f"\nKẾT QUẢ: FAIL - {msg}")

"""sysinfo.py —— 服务器信息快照（只读 /proc、/sys、shutil，**零网络**）。

容器里跑时 `/proc/meminfo`、`os.cpu_count()` 报的是**宿主**的值，所以额外读 cgroup 配额：
两个都返回（宿主 vs 容器上限），面板上分开展示，避免「明明限了 2 核却显示 64 核」这种误读。
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import time

START = time.time()          # 本进程启动时刻（模块导入即记，用于「本服务已运行」）

_CGROUP_MAX_FILES = ("/sys/fs/cgroup/memory.max",
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _os_pretty() -> str:
    """发行版名字，如 Ubuntu 24.04.1 LTS；读不到就退化成内核名。"""
    for line in _read("/etc/os-release").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return platform.system() or "—"


def _mem() -> dict:
    info: dict[str, int] = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if v:
            try:
                info[k.strip()] = int(v.split()[0]) * 1024
            except ValueError:
                continue
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(0, total - avail)
    return {"total": total, "used": used, "avail": avail,
            "percent": round(used * 100 / total) if total else 0}


def _cgroup_mem_limit() -> int | None:
    """容器内存上限；不限（宿主可见全部）返回 None。"""
    for p in _CGROUP_MAX_FILES:
        v = _read(p)
        if not v or v == "max":
            continue
        try:
            n = int(v)
        except ValueError:
            continue
        if 0 < n < (1 << 62):
            return n
    return None


def _cgroup_cpu_quota() -> float | None:
    """容器 CPU 上限（核数）；不限返回 None。cgroup v2: cpu.max = '800000 100000' → 8 核。"""
    v = _read("/sys/fs/cgroup/cpu.max")
    if v and not v.startswith("max"):
        parts = v.split()
        try:
            return round(int(parts[0]) / int(parts[1] or 100000), 2)
        except (ValueError, IndexError):
            pass
    try:                                    # cgroup v1 兜底
        quota, per = int(_read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")), \
            int(_read("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
        if quota > 0 and per > 0:
            return round(quota / per, 2)
    except ValueError:
        pass
    return None


def _cpu_model() -> str:
    """CPU 型号。x86 在 /proc/cpuinfo 的 model name，ARM 机器通常只有 CPU part / Hardware。"""
    keys = ("model name", "hardware", "cpu model")     # 注意别匹配到 "processor : 0"
    for want in keys:
        for line in _read("/proc/cpuinfo").splitlines():
            k, _, v = line.partition(":")
            if k.strip().lower() == want and v.strip():
                return v.strip()
    return ""


def _load() -> list[float] | None:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def _uptime() -> int:
    try:
        return int(float(_read("/proc/uptime").split()[0]))
    except (ValueError, IndexError):
        return 0


def _local_ip() -> str:
    """本机 IP。不联网：UDP connect 只是让内核选路，不发任何包。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def _disk(path: str) -> dict:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return {}
    return {"path": path, "total": u.total, "used": u.used, "free": u.free,
            "percent": round(u.used * 100 / u.total) if u.total else 0}


def _tz() -> str:
    name, off = time.strftime("%Z"), time.strftime("%z")
    if off:
        off = "UTC" + off[:3] + ":" + off[3:]
    return f"{name} {off}".strip() or "—"


def collect(db_path: str = "") -> dict:
    """服务器信息快照。所有字段都是只读探测，失败一律降级成空值，绝不抛错。"""
    cpu_model = _cpu_model()
    now = time.time()
    try:
        dbfile = os.path.getsize(db_path) if db_path else 0
    except OSError:
        dbfile = 0
    return {
        "hostname": socket.gethostname(),
        "os": _os_pretty(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "docker": os.path.exists("/.dockerenv"),
        "ip": _local_ip(),
        "tz": _tz(),
        "now": int(now),
        "now_str": time.strftime("%Y-%m-%d %H:%M:%S"),   # 服务器本地时间（和 tz 同一个时区）
        "cpu": {"count": os.cpu_count() or 0, "model": cpu_model,
                "load": _load(), "quota": _cgroup_cpu_quota()},
        "mem": {**_mem(), "limit": _cgroup_mem_limit()},
        "disk": _disk(os.path.dirname(db_path) if db_path else "/"),
        "uptime": {"host": _uptime(), "process": int(now - START)},
        "dbfile": dbfile,
    }

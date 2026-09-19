"""sysinfo（服务器信息）回归：字段齐、类型对、探测失败一律降级不抛错。"""
from __future__ import annotations

from app import sysinfo


def test_collect_shape(tmp_path):
    d = sysinfo.collect(str(tmp_path / "none.db"))
    assert isinstance(d["hostname"], str) and d["hostname"]
    assert d["python"].count(".") == 2
    assert isinstance(d["docker"], bool)
    assert isinstance(d["tz"], str) and d["tz"]
    assert d["now"] > 0
    assert d["cpu"]["count"] >= 1
    assert d["mem"]["total"] > 0
    assert 0 <= d["mem"]["percent"] <= 100
    assert d["mem"]["used"] + d["mem"]["avail"] == d["mem"]["total"]
    assert d["disk"]["total"] > 0 and 0 <= d["disk"]["percent"] <= 100
    assert d["uptime"]["host"] >= 0 and d["uptime"]["process"] >= 0
    assert d["dbfile"] == 0                       # 文件不存在 → 0，不抛


def test_collect_measures_db_file(tmp_path):
    p = tmp_path / "y.db"
    p.write_bytes(b"x" * 1234)
    assert sysinfo.collect(str(p))["dbfile"] == 1234


def test_collect_without_db_path(tmp_path):
    d = sysinfo.collect("")
    assert d["dbfile"] == 0
    assert d["disk"]["total"] > 0                 # 退化成 /


def test_cpu_usage_needs_two_samples():
    """CPU 占用率靠两次采样差值：第一次没基准 → None，第二次是 0~100 的整数。"""
    first = sysinfo._cpu_usage()
    assert first is None or 0 <= first <= 100
    second = sysinfo._cpu_usage()
    assert second is None or 0 <= second <= 100


def test_helpers_never_raise():
    assert sysinfo._read("/no/such/file/at/all") == ""
    assert sysinfo._cgroup_mem_limit() is None or sysinfo._cgroup_mem_limit() > 0
    q = sysinfo._cgroup_cpu_quota()
    assert q is None or q > 0
    assert sysinfo._local_ip()
    assert isinstance(sysinfo._cpu_model(), str)
    assert sysinfo._os_pretty()
    assert sysinfo._uptime() >= 0
    assert sysinfo._load() is None or len(sysinfo._load()) == 3

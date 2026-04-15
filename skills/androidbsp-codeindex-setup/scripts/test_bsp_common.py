"""Tests for _bsp_common.py. Run: pytest test_bsp_common.py -v"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import _bsp_common as c  # type: ignore[import-not-found] # noqa: E402


def test_version_is_packaging_version():
    from packaging.version import Version
    assert isinstance(c.BSP_COMMON_VERSION, Version)


def test_find_bsp_root_locates_envsetup(tmp_path):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "envsetup.sh").touch()
    sub = tmp_path / "drivers" / "pci"
    sub.mkdir(parents=True)
    assert c.find_bsp_root(sub) == tmp_path


def test_find_bsp_root_raises_when_missing(tmp_path):
    with pytest.raises(c.BSPRootNotFound):
        c.find_bsp_root(tmp_path)


def test_load_active_files_returns_set(tmp_path):
    codenav = tmp_path / ".codenav"
    codenav.mkdir()
    (codenav / "active_files.idx").write_text("a/b.c\nc/d.h\n")
    files = c.load_active_files(tmp_path)
    assert files == {"a/b.c", "c/d.h"}


def test_load_active_files_missing_returns_none(tmp_path):
    assert c.load_active_files(tmp_path) is None


def test_first_existing(tmp_path):
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    p2.mkdir()
    assert c.first_existing([p1, p2]) == p2
    assert c.first_existing([p1]) is None


def test_scan_partitions_returns_existing(tmp_path):
    for part in ("system", "vendor"):
        d = tmp_path / part / "etc" / "init"
        d.mkdir(parents=True)
    found = c.scan_partitions(tmp_path, "etc/init")
    found_names = {p.parent.parent.name for p in found}
    assert found_names == {"system", "vendor"}


def test_run_cmd_captures_stdout():
    r = c.run_cmd(["echo", "hello"])
    assert r.returncode == 0
    assert "hello" in r.stdout


def test_run_cmd_timeout():
    r = c.run_cmd(["sleep", "5"], timeout=1)
    # timeout 不抛异常，返回非零；调用方决定如何处理
    assert r.returncode != 0


def test_finding_dataclass_serializes():
    f = c.Finding(tag="DECL", file="foo.c", line=10, snippet="int x;",
                  info={"k": "v"})
    d = c.finding_to_dict(f)
    assert d["tag"] == "DECL"
    assert d["info"] == {"k": "v"}


def test_emitter_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "envsetup.sh").touch()
    (tmp_path / ".codenav").mkdir()

    import argparse
    args = argparse.Namespace(json=False, no_events=False, root=tmp_path,
                              timeout=120)
    with c.Emitter(args, "test_script.py") as e:
        e.emit(c.Finding(tag="X", file="a.c", line=1, snippet="hi"),
               confidence="high", source="static-rg", tags=["t"])

    log = (tmp_path / ".codenav" / "events.jsonl").read_text()
    assert log.strip()
    rec = json.loads(log.strip())
    assert rec["schema"] == "androidbsp.event/v1"
    assert rec["source"] == "static-rg"
    assert rec["confidence"] == "high"
    assert rec["finding"]["tag"] == "X"


def test_emitter_no_events_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "envsetup.sh").touch()
    (tmp_path / ".codenav").mkdir()

    import argparse
    args = argparse.Namespace(json=False, no_events=True, root=tmp_path,
                              timeout=120)
    with c.Emitter(args, "test_script.py") as e:
        e.emit(c.Finding(tag="X", file="a.c", line=1, snippet="hi"))

    log_path = tmp_path / ".codenav" / "events.jsonl"
    assert not log_path.exists() or log_path.read_text() == ""


def test_make_parser_has_common_flags():
    p = c.make_parser("test")
    args = p.parse_args(["--json", "--no-events", "--timeout", "60"])
    assert args.json is True
    assert args.no_events is True
    assert args.timeout == 60


def test_require_version_passes():
    c.require_version("0.0.1")  # should not raise


def test_require_version_fails():
    with pytest.raises(RuntimeError):
        c.require_version("99.0.0")

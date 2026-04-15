# Android BSP Code-Nav Harden Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 加固三个 Android BSP code-nav skill（codeindex / codecross / domaintrace）+ 引入 meta-skill `androidbsp-codenav`，达到 Android 11+ 普适性，为未来 runtime-trace skill 留接口，并在 `/home/leo/atk-rk3568_androidR_release_v1.4_20250104/` 上验证。

**Architecture:** 三 skill 行为对称化（标记块注入 AGENTS.md、共享 `.codenav/scripts/_bsp_common.py`、统一 JSON + `.codenav/events.jsonl` 输出契约）。Meta-skill 仅做编排。普适性靠"特征探测而非版本探测"。

**Tech Stack:** Python 3.10+（dataclasses、pathlib、argparse、subprocess、`packaging.version`、`jsonschema`）、ripgrep、universal-ctags-based gtags、bash。

**Spec reference:** `docs/superpowers/specs/2026-04-15-androidbsp-codenav-harden-design.md`

**目标 BSP**: `/home/leo/atk-rk3568_androidR_release_v1.4_20250104/`（RK3568 / Android R / SDK 30）

---

## Task 1: 清理 __pycache__ + 添加 .gitignore

**Files:**
- Delete: `skills/androidbsp-codeindex-setup/scripts/__pycache__/`
- Create: `.gitignore`（仓库根，若已有则追加）

- [ ] **Step 1: 删除脏文件**

```bash
rm -rf skills/androidbsp-codeindex-setup/scripts/__pycache__
```

- [ ] **Step 2: 写 .gitignore**

如果仓库根没有 `.gitignore`，新建；如果已有，追加（去重）：

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
*.pyd

# Editors
.vscode/
.idea/
*.swp
*.swo

# Local validation outputs (machine-specific)
skills/_validation/baseline_atk/
skills/_validation/run_*/
```

- [ ] **Step 3: 验证清理**

```bash
find . -name __pycache__ -not -path './.git/*' -print
```
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: add .gitignore covering __pycache__ and validation outputs"
```

---

## Task 2: Phase 0 · 部署当前三 skill 到 atk（基线采集前置）

**Files:** 不改本仓库代码；操作目标 `/home/leo/atk-rk3568_androidR_release_v1.4_20250104/`

**说明**：此任务把**当前未加固**的三个 skill 原样部署到 atk，作为 Phase 0 基线。Phase N 会重新部署加固后的版本。

- [ ] **Step 1: 验证 atk 满足 codeindex 前置**

```bash
cd /home/leo/atk-rk3568_androidR_release_v1.4_20250104
[ -f build/envsetup.sh ] && [ -d device ] && echo "OK: BSP marker"
[ -d out ] && [ -n "$(ls -A out)" ] && echo "OK: built"
ls out/combined-*.ninja 2>/dev/null | head -1 || ls out/soong/build.ninja 2>/dev/null
ls -d out/target/product/*/ 2>/dev/null | head -3
```
Expected: 全部输出非空

- [ ] **Step 2: 跑 codeindex 当前流程**

按照 `skills/androidbsp-codeindex-setup/SKILL.md` 当前内容，执行 Phase 1-5（工具安装 + bsp_filter_gen + compdb + gtags + clangd + AGENTS.md）。

```bash
SKILL_DIR=/home/leo/ai-config-hub/skills/androidbsp-codeindex-setup
BSP_ROOT=/home/leo/atk-rk3568_androidR_release_v1.4_20250104

cd $BSP_ROOT
python3 $SKILL_DIR/deps/install_tools.py --check-only

mkdir -p scripts
cp $SKILL_DIR/scripts/bsp_filter_gen.py scripts/
cp $SKILL_DIR/scripts/arg.sh scripts/
chmod +x scripts/bsp_filter_gen.py scripts/arg.sh

python3 scripts/bsp_filter_gen.py \
  -b "source build/envsetup.sh && lunch atk_rk3568_r-userdebug" \
  > .active_files.idx
wc -l .active_files.idx

# compdb（项目可能有 gen_compdb.py，没有则手动）
[ -x ./gen_compdb.py ] && python3 gen_compdb.py || \
  ./prebuilts/build-tools/linux-x86/bin/ninja -f out/combined-*.ninja -t compdb \
    $(awk '/^rule /{print $2}' out/combined-*.ninja | grep -iE 'cc|cxx|clang') \
    > compile_commands.json

# gtags
grep -E '\.(c|h|cc|cpp|cxx|hpp|java|kt|S|s|asm)$' .active_files.idx > gtags.files
GTAGSLABEL=new-ctags gtags -v -f gtags.files 2>&1 | tee gtags.log

# clangd 模板
[ -f .clangd ] || cp $SKILL_DIR/assets/clangd.template .clangd

# AGENTS.md（注意：当前是覆盖式）
cp $SKILL_DIR/assets/AGENTS.md.template AGENTS.md
[ -f CLAUDE.md ] && grep -q '@AGENTS.md' CLAUDE.md || echo '@AGENTS.md' >> CLAUDE.md
```

如果 lunch target 名称不对，根据 `device/rockchip/` 下实际目录调整。

- [ ] **Step 3: 跑 codecross 当前流程**

```bash
SKILL_DIR=/home/leo/ai-config-hub/skills/androidbsp-codecross-setup
cd $BSP_ROOT
cp $SKILL_DIR/scripts/jni_bridge.py     scripts/
cp $SKILL_DIR/scripts/aidl_bridge.py    scripts/
cp $SKILL_DIR/scripts/syscall_trace.py  scripts/
cp $SKILL_DIR/scripts/ioctl_trace.py    scripts/
cp $SKILL_DIR/scripts/xlang_find.py     scripts/
chmod +x scripts/*.py
cat $SKILL_DIR/assets/AGENTS.md.codecross.template >> AGENTS.md
```

- [ ] **Step 4: 跑 alltrace 当前流程**

```bash
SKILL_DIR=/home/leo/ai-config-hub/skills/androidbsp-alltrace-setup
cd $BSP_ROOT
for f in dt_bind sysfs_attr binder_svc selinux_trace subsys_trace prop_trace \
         build_trace initrc_trace kconfig_trace firmware_trace netlink_trace \
         media_topo domain_find; do
  cp $SKILL_DIR/scripts/$f.py scripts/
done
chmod +x scripts/*.py
cat $SKILL_DIR/assets/AGENTS.md.alltrace.template >> AGENTS.md
```

- [ ] **Step 5: 冒烟验证**

```bash
cd $BSP_ROOT
python3 -c "import json; d=json.load(open('compile_commands.json')); print(f'compdb: {len(d)}')"
[ -f GTAGS ] && echo "gtags OK"
python3 scripts/xlang_find.py --help >/dev/null && echo "xlang_find OK"
python3 scripts/domain_find.py --help >/dev/null && echo "domain_find OK"
```
Expected: 全部 OK

**No commit**（仅 atk 上的部署，不动本仓库）

---

## Task 3: Phase 0 · 跑 24 条基线查询 + 保存输出

**Files:**
- Create: `skills/_validation/run_baseline.sh`
- Create: `skills/_validation/baseline_atk/`（目录，被 .gitignore 覆盖）

- [ ] **Step 1: 写基线驱动脚本**

```bash
mkdir -p skills/_validation
```

Create `skills/_validation/run_baseline.sh`:

```bash
#!/usr/bin/env bash
# 在 atk BSP 上跑 24 条基线查询，输出存到指定目录
set -uo pipefail
BSP_ROOT="${1:?usage: $0 <bsp_root> <out_dir>}"
OUT_DIR="${2:?usage: $0 <bsp_root> <out_dir>}"
mkdir -p "$OUT_DIR"
cd "$BSP_ROOT"

run_query() {
  local id="$1" cmd="$2" desc="$3"
  local out="$OUT_DIR/${id}.txt"
  {
    echo "=== ID: $id"
    echo "=== DESC: $desc"
    echo "=== CMD: $cmd"
    echo "=== START: $(date -Iseconds)"
    local t0=$(date +%s.%N)
    eval "$cmd" 2> "${out}.stderr"
    local rc=$?
    local t1=$(date +%s.%N)
    echo "=== EXIT: $rc"
    echo "=== ELAPSED: $(echo "$t1 - $t0" | bc)s"
    echo "=== STDERR:"
    cat "${out}.stderr"
    echo "=== STDOUT:"
  } > "$out"
  eval "$cmd" 2>/dev/null >> "$out"
  rm -f "${out}.stderr"
}

run_query 01 'bash scripts/arg.sh "rockchip_pcie_probe"' 'arg pcie probe (expect ≥1)'
run_query 02 'bash scripts/arg.sh "definitely_nonexistent_symbol_xyz123"' 'arg empty (expect 0 hits, exit 0)'
run_query 03 'global -r printk | head -200' 'gtags printk refs (huge, no hang)'
run_query 04 'python3 scripts/jni_bridge.py JNI_OnLoad' 'jni framework entries'
run_query 05 'python3 scripts/aidl_bridge.py ICameraProvider' 'HIDL camera provider'
run_query 06 'python3 scripts/aidl_bridge.py IRadio' 'AIDL Radio path'
run_query 07 'python3 scripts/syscall_trace.py openat' 'syscall both sides'
run_query 08 'python3 scripts/ioctl_trace.py BINDER_WRITE_READ' 'binder ioctl'
run_query 09 'python3 scripts/dt_bind.py rockchip,rk3568-pcie' 'DT both sides'
run_query 10 'python3 scripts/sysfs_attr.py current_temp' 'thermal attr'
run_query 11 'python3 scripts/binder_svc.py android.hardware.camera.provider' 'svc registration'
run_query 12 'python3 scripts/binder_svc.py ICameraProvider' 'svc by interface name'
run_query 13 'python3 scripts/selinux_trace.py untrusted_app' 'te + contexts'
run_query 14 'python3 scripts/subsys_trace.py clk_pcie_aux' 'clock prov/cons/DT'
run_query 15 'python3 scripts/prop_trace.py ro.product.model' 'prop multi-source'
run_query 16 'python3 scripts/prop_trace.py ro.vendor.region' 'prop multi-partition'
run_query 17 'python3 scripts/build_trace.py libbinder' 'bp + install'
run_query 18 'python3 scripts/initrc_trace.py vendor.power.stats' 'init trigger+service'
run_query 19 'python3 scripts/kconfig_trace.py CONFIG_DRM_ROCKCHIP' 'kconfig defconfig+ifdef'
run_query 20 'python3 scripts/firmware_trace.py rk_tb_8852be_fw.bin' 'firmware request+pkg'
run_query 21 'python3 scripts/netlink_trace.py NL80211' 'netlink family+userspace'
run_query 22 'python3 scripts/media_topo.py rkisp' 'V4L2 subdev+pad'
run_query 23 'python3 scripts/xlang_find.py openat' 'xlang dispatcher to syscall'
run_query 24 'python3 scripts/domain_find.py rockchip,rk3568-pcie' 'domain dispatcher to dt'

echo "Done. Outputs in $OUT_DIR"
```

```bash
chmod +x skills/_validation/run_baseline.sh
```

- [ ] **Step 2: 跑基线**

```bash
./skills/_validation/run_baseline.sh \
  /home/leo/atk-rk3568_androidR_release_v1.4_20250104 \
  skills/_validation/baseline_atk
```

- [ ] **Step 3: 人工抽查 5 条**

```bash
ls -la skills/_validation/baseline_atk/ | head
head -50 skills/_validation/baseline_atk/01.txt
head -50 skills/_validation/baseline_atk/09.txt
head -50 skills/_validation/baseline_atk/13.txt
head -50 skills/_validation/baseline_atk/17.txt
head -50 skills/_validation/baseline_atk/19.txt
```

记录到 `skills/_validation/baseline_atk/PHASE0_NOTES.md`：
- 每条查询的 EXIT、ELAPSED、命中行数（用 `wc -l` 数 STDOUT 段）
- 看上去合理 / 看上去为零（标记为"已知零命中，待 Phase 1 修"）/ 看上去乱码

- [ ] **Step 4: 清理 atk 上部署的旧版本**

不需要清理——Task 21（Phase N 重部署）会覆盖。`scripts/` 目录里的旧脚本会被新版本替换。

**No commit**（baseline_atk/ 在 .gitignore 内，不入库；只有 `run_baseline.sh` 进库）

- [ ] **Step 5: 提交 run_baseline.sh**

```bash
git add skills/_validation/run_baseline.sh
git commit -m "test: add Phase 0/N baseline runner for atk validation"
```

---

## Task 4: 重命名 alltrace → domaintrace

**Files:**
- Move: `skills/androidbsp-alltrace-setup/` → `skills/androidbsp-domaintrace-setup/`
- Modify: 全仓库所有 alltrace 字面量

- [ ] **Step 1: 目录改名**

```bash
cd /home/leo/ai-config-hub
git mv skills/androidbsp-alltrace-setup skills/androidbsp-domaintrace-setup
```

- [ ] **Step 2: 文件改名**

```bash
cd skills/androidbsp-domaintrace-setup
git mv assets/AGENTS.md.alltrace.template assets/AGENTS.md.domaintrace.template
```

- [ ] **Step 3: 替换字面量**

```bash
cd /home/leo/ai-config-hub
# 列出所有出现 alltrace 的文件
grep -rl 'alltrace' skills/ docs/ --exclude-dir=__pycache__
```

对每个文件中的字面量做以下替换：

| 旧 | 新 |
|---|---|
| `androidbsp-alltrace-setup` | `androidbsp-domaintrace-setup` |
| `AGENTS.md.alltrace.template` | `AGENTS.md.domaintrace.template` |
| `<!-- BEGIN: androidbsp-alltrace-setup -->` | `<!-- BEGIN: androidbsp-domaintrace-setup v=1 -->` |
| `<!-- END: androidbsp-alltrace-setup -->` | `<!-- END: androidbsp-domaintrace-setup -->` |
| `/alltrace --setup` | `/domaintrace setup` |
| `/alltrace` (作为命令) | `/domaintrace` |
| `name: alltrace` | `name: domaintrace` |

可以用 sed 批量（注意先 dry-run）：

```bash
# Dry-run
grep -rn 'alltrace' skills/ docs/ --exclude-dir=__pycache__ | head -50

# 实际替换（保留 git history，逐文件）
for f in $(grep -rl 'alltrace' skills/ docs/ --exclude-dir=__pycache__); do
  sed -i 's|androidbsp-alltrace-setup|androidbsp-domaintrace-setup|g; \
          s|AGENTS\.md\.alltrace\.template|AGENTS.md.domaintrace.template|g; \
          s|/alltrace --setup|/domaintrace setup|g; \
          s|/alltrace\b|/domaintrace|g' "$f"
done
```

- [ ] **Step 4: 修 SKILL.md 描述里的触发词**

打开 `skills/androidbsp-domaintrace-setup/SKILL.md`，把 frontmatter 的 `description` 末尾的旧触发词更新：
- `「部署 alltrace」` → `「部署 domaintrace」`
- `/alltrace --setup` → `/domaintrace setup`
- `command: /alltrace` → `command: /domaintrace`
- `name: --setup` → `name: setup`（去 `--`）

- [ ] **Step 5: 同步修 codecross 命令风格**

打开 `skills/androidbsp-codecross-setup/SKILL.md` frontmatter：
- `command: /code-cross` 不变（用户已习惯这个名）
- `name: --setup` → `name: setup`
- description 里 `/code-cross --setup` → `/code-cross setup`

- [ ] **Step 6: 验证零残留**

```bash
grep -rn 'alltrace' skills/ docs/ --exclude-dir=__pycache__
```
Expected: 空输出（除了本 plan 文档自己提到的，可暂忽略）

```bash
# 也检查 -- 风格的 setup 命令残留
grep -rn -- '--setup' skills/androidbsp-codecross-setup/SKILL.md skills/androidbsp-domaintrace-setup/SKILL.md
```
Expected: 空（可能在描述文字里有，不影响命令解析）

- [ ] **Step 7: Commit**

```bash
git add -A skills/ docs/
git commit -m "refactor: rename alltrace to domaintrace, unify subcommand style"
```

---

## Task 5: 写 _bsp_common.py 公共库

**Files:**
- Create: `skills/androidbsp-codeindex-setup/scripts/_bsp_common.py`
- Create: `skills/androidbsp-codeindex-setup/scripts/test_bsp_common.py`

- [ ] **Step 1: 写测试**（先写测试，TDD）

Create `skills/androidbsp-codeindex-setup/scripts/test_bsp_common.py`:

```python
"""Tests for _bsp_common.py. Run: pytest test_bsp_common.py -v"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import _bsp_common as c


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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd skills/androidbsp-codeindex-setup/scripts/
pip install --user pytest packaging  # 一次性
pytest test_bsp_common.py -v
```
Expected: ImportError (no `_bsp_common` module)

- [ ] **Step 3: 写实现**

Create `skills/androidbsp-codeindex-setup/scripts/_bsp_common.py`:

```python
"""
Shared primitives for androidbsp code-nav scripts.
Deployed by androidbsp-codeindex-setup to $BSP_ROOT/.codenav/scripts/.
All other scripts in the same dir import from here.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from packaging.version import Version

# ─── Version ───────────────────────────────────────────────────
BSP_COMMON_VERSION = Version("1.0.0")
SCHEMA_FINDING = "androidbsp.finding/v1"
SCHEMA_EVENT = "androidbsp.event/v1"

DEFAULT_TIMEOUT = 120
PARTITIONS = ["system", "vendor", "odm", "system_ext", "product"]
CODENAV_DIRNAME = ".codenav"


class BSPRootNotFound(RuntimeError):
    pass


# ─── Artifact discovery ────────────────────────────────────────
def find_bsp_root(start: Optional[Path] = None) -> Path:
    """Walk up from `start` looking for build/envsetup.sh. Default cwd."""
    cur = (start or Path.cwd()).resolve()
    while True:
        if (cur / "build" / "envsetup.sh").exists():
            return cur
        if cur.parent == cur:
            raise BSPRootNotFound(f"no build/envsetup.sh from {start}")
        cur = cur.parent


def load_active_files(bsp_root: Path) -> Optional[set[str]]:
    """Read .codenav/active_files.idx; return set of relative paths or None."""
    p = bsp_root / CODENAV_DIRNAME / "active_files.idx"
    if not p.exists():
        return None
    return {ln.strip() for ln in p.read_text().splitlines() if ln.strip()}


def parse_compile_commands(bsp_root: Path) -> list[dict]:
    """Read compile_commands.json at root. Return [] on failure with WARN."""
    p = bsp_root / "compile_commands.json"
    if not p.exists():
        print(f"WARN: no compile_commands.json at {bsp_root}", file=sys.stderr)
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARN: cannot parse compdb: {e}", file=sys.stderr)
        return []


# ─── Multi-partition / multi-candidate ────────────────────────
def first_existing(candidates: Iterable[Path]) -> Optional[Path]:
    """Return first existing path or None."""
    for p in candidates:
        if p.exists():
            return p
    return None


def scan_partitions(bsp_root: Path, subpath: str) -> list[Path]:
    """For each Android partition (system/vendor/odm/system_ext/product),
    return existing $bsp_root/<part>/<subpath> paths."""
    found = []
    for part in PARTITIONS:
        candidate = bsp_root / part / subpath
        if candidate.exists():
            found.append(candidate)
    return found


# ─── subprocess wrapper ───────────────────────────────────────
def run_cmd(cmd: list[str], *, timeout: int = DEFAULT_TIMEOUT,
            cwd: Optional[Path] = None,
            check: bool = False) -> subprocess.CompletedProcess:
    """Run cmd capturing stdout/stderr. Timeout returns non-zero, no exception."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd, check=check)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            args=cmd, returncode=124,
            stdout=e.stdout.decode() if e.stdout else "",
            stderr=f"TIMEOUT after {timeout}s"
        )


# ─── Search wrappers ──────────────────────────────────────────
def rg_find(pattern: str, *, globs: Optional[list[str]] = None,
            root: Optional[Path] = None,
            extra: Optional[list[str]] = None,
            timeout: int = DEFAULT_TIMEOUT) -> list[tuple[str, int, str]]:
    """Run `rg -n --no-heading <pattern>`. Return [(file, line, snippet)]."""
    cmd = ["rg", "-n", "--no-heading"]
    for g in globs or []:
        cmd += ["-g", g]
    cmd += extra or []
    cmd.append(pattern)
    if root:
        cmd.append(str(root))
    r = run_cmd(cmd, timeout=timeout)
    if r.returncode not in (0, 1):  # 1 = no match (rg convention)
        print(f"WARN: rg exited {r.returncode}: {r.stderr.strip()}",
              file=sys.stderr)
        return []
    out = []
    for line in r.stdout.splitlines():
        # format: file:line:snippet
        parts = line.split(":", 2)
        if len(parts) == 3:
            try:
                out.append((parts[0], int(parts[1]), parts[2]))
            except ValueError:
                pass
    return out


def gtags_lookup(symbol: str, *, kind: str = "def",
                 root: Optional[Path] = None,
                 timeout: int = DEFAULT_TIMEOUT
                 ) -> list[tuple[str, int, str]]:
    """global wrapper. kind: def (-d), ref (-r), path (-P)."""
    flag = {"def": "-d", "ref": "-r", "path": "-P"}.get(kind, "-d")
    r = run_cmd(["global", flag, "-x", symbol], cwd=root, timeout=timeout)
    if r.returncode not in (0, 1):
        return []
    out = []
    for line in r.stdout.splitlines():
        # format: symbol lineno path snippet
        parts = line.split(None, 3)
        if len(parts) >= 4:
            try:
                out.append((parts[2], int(parts[1]), parts[3]))
            except ValueError:
                pass
    return out


# ─── Output structures ────────────────────────────────────────
@dataclass
class Finding:
    tag: str
    file: str
    line: int = 0
    snippet: str = ""
    info: dict = field(default_factory=dict)


def finding_to_dict(f: Finding) -> dict:
    d = asdict(f)
    d["schema"] = SCHEMA_FINDING
    return d


class Emitter:
    """Context manager: routes Findings to stdout (TSV or JSONL) and
    optionally appends Events to .codenav/events.jsonl.

    Usage:
        with Emitter(args, 'dt_bind.py') as e:
            e.emit(Finding(tag='DT', file='...', line=10, snippet='...'),
                   confidence='med', source='static-rg', tags=['dt'])
    """
    SCRIPT_VERSION = "1.0.0"  # bump per script if needed; default

    def __init__(self, args: argparse.Namespace, script_name: str):
        self.args = args
        self.script_name = script_name
        self.as_json = getattr(args, "json", False)
        self.no_events = getattr(args, "no_events", False)
        self.bsp_root = self._resolve_root(args)
        self._fp = None  # events.jsonl handle, lazy open

    def _resolve_root(self, args) -> Optional[Path]:
        try:
            return Path(getattr(args, "root", None) or find_bsp_root())
        except BSPRootNotFound:
            return None

    def __enter__(self):
        if not self.no_events and self.bsp_root:
            codenav = self.bsp_root / CODENAV_DIRNAME
            codenav.mkdir(exist_ok=True)
            self._fp = open(codenav / "events.jsonl", "a", buffering=1)
        return self

    def __exit__(self, *a):
        if self._fp:
            self._fp.close()

    def emit(self, finding: Finding, *, confidence: str = "med",
             source: str = "static-rg", tags: Optional[list[str]] = None
             ) -> None:
        # 1) stdout
        if self.as_json:
            sys.stdout.write(json.dumps(finding_to_dict(finding)) + "\n")
        else:
            info_str = " ".join(f"{k}={v}" for k, v in finding.info.items())
            sys.stdout.write(
                f"{finding.tag}\t{finding.file}:{finding.line}\t"
                f"{finding.snippet}\t{info_str}\n"
            )
        # 2) events.jsonl
        if self._fp:
            event = {
                "schema": SCHEMA_EVENT,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": source,
                "script": self.script_name,
                "script_version": self.SCRIPT_VERSION,
                "query": {
                    "args": sys.argv[1:],
                    "cwd": str(Path.cwd()),
                },
                "finding": finding_to_dict(finding),
                "confidence": confidence,
                "tags": tags or [],
            }
            self._fp.write(json.dumps(event, ensure_ascii=False) + "\n")


# ─── argparse helper ──────────────────────────────────────────
def make_parser(description: str) -> argparse.ArgumentParser:
    """ArgumentParser with --root, --json, --no-events, --timeout pre-injected."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--root", type=Path, default=None,
                   help="BSP root (default: auto-detect from cwd)")
    p.add_argument("--json", action="store_true",
                   help="emit JSONL on stdout instead of TSV")
    p.add_argument("--no-events", action="store_true",
                   help="do not append to .codenav/events.jsonl")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help=f"per-subprocess timeout in seconds (default {DEFAULT_TIMEOUT})")
    return p


# ─── Version compatibility ────────────────────────────────────
def require_version(min_version: str) -> None:
    if BSP_COMMON_VERSION < Version(min_version):
        raise RuntimeError(
            f"_bsp_common version {BSP_COMMON_VERSION} < required {min_version}"
        )
```

- [ ] **Step 4: 跑测试**

```bash
cd skills/androidbsp-codeindex-setup/scripts/
pytest test_bsp_common.py -v
```
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add skills/androidbsp-codeindex-setup/scripts/_bsp_common.py \
        skills/androidbsp-codeindex-setup/scripts/test_bsp_common.py
git commit -m "feat(bsp-common): introduce shared library with Emitter and JSONL events"
```

---

## Task 6: codeindex 部署 _bsp_common.py + .codenav/ 布局

**Files:**
- Modify: `skills/androidbsp-codeindex-setup/SKILL.md`（Phase 2 改造、Phase 5 改造）
- Modify: `skills/androidbsp-codeindex-setup/scripts/bsp_filter_gen.py`（路径改 .codenav/）
- Modify: `skills/androidbsp-codeindex-setup/scripts/arg.sh`（路径改 .codenav/）

- [ ] **Step 1: 改 bsp_filter_gen.py 输出路径**

打开 `skills/androidbsp-codeindex-setup/scripts/bsp_filter_gen.py`，找到默认输出位置（之前 stdout，由 SKILL.md `>` 重定向到 `.active_files.idx`）。改为支持 `--output` 参数，默认 `$BSP_ROOT/.codenav/active_files.idx`，**写入前先把已存在的同名文件 mv 成 `active_files.idx.prev`**：

在 main 函数末尾找到写出逻辑，替换为：

```python
def write_index(lines: list[str], bsp_root: Path,
                output: Optional[Path] = None) -> Path:
    out = output or (bsp_root / ".codenav" / "active_files.idx")
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        prev = out.with_suffix(".idx.prev")
        out.rename(prev)
        print(f"INFO: backed up previous index to {prev.name}",
              file=sys.stderr)
    out.write_text("\n".join(lines) + "\n")
    return out
```

并把 argparse 加：

```python
parser.add_argument("--output", type=Path, default=None,
                    help="output path (default: $BSP_ROOT/.codenav/active_files.idx)")
parser.add_argument("--root", type=Path, default=Path.cwd(),
                    help="BSP root (default: cwd)")
```

main 末尾把 print 改成 write_index 调用。保留 stdout fallback：传 `--output -` 时输出到 stdout（兼容旧 SKILL.md 的 `>` 重定向方式，但新 SKILL.md 不再用）。

- [ ] **Step 2: 改 arg.sh 路径**

```bash
sed -n '1,32p' skills/androidbsp-codeindex-setup/scripts/arg.sh
```

找到引用 `.active_files.idx` 的行，改为：

```bash
ACTIVE_IDX="${BSP_ROOT:-$(pwd)}/.codenav/active_files.idx"
[ -f "$ACTIVE_IDX" ] || ACTIVE_IDX="$(pwd)/.active_files.idx"  # 兼容旧布局
```

- [ ] **Step 3: 改 codeindex SKILL.md 的 Phase 2**

打开 `skills/androidbsp-codeindex-setup/SKILL.md`，把 Phase 2 改成：

```markdown
## Phase 2 — 过滤未编译模块 → `.codenav/active_files.idx`

```bash
cd $BSP_ROOT
mkdir -p .codenav/scripts

# 部署公共库（仅当本地版本旧于 skill 自带版本）
TARGET=.codenav/scripts/_bsp_common.py
SRC=$SKILL_DIR/scripts/_bsp_common.py
if [ -f "$TARGET" ]; then
  CUR=$(python3 -c "import sys; sys.path.insert(0,'.codenav/scripts'); \
                    import _bsp_common as c; print(c.BSP_COMMON_VERSION)")
  NEW=$(python3 -c "import sys; sys.path.insert(0,'$SKILL_DIR/scripts'); \
                    import _bsp_common as c; print(c.BSP_COMMON_VERSION)")
  python3 -c "from packaging.version import Version as V; \
              import sys; sys.exit(0 if V('$CUR') < V('$NEW') else 1)" \
    && cp $SRC $TARGET && echo "INFO: _bsp_common.py upgraded $CUR → $NEW" \
    || echo "INFO: _bsp_common.py is $CUR (>= $NEW), skip"
else
  cp $SRC $TARGET
  echo "INFO: _bsp_common.py installed"
fi

# 部署本 skill 的 user-facing 脚本
cp $SKILL_DIR/scripts/bsp_filter_gen.py .codenav/scripts/
cp $SKILL_DIR/scripts/arg.sh            .codenav/scripts/
cp $SKILL_DIR/scripts/idx_diff.py       .codenav/scripts/   # Task 17 后存在
chmod +x .codenav/scripts/*.py .codenav/scripts/*.sh

# 生成 active_files.idx（脚本内部默认写到 .codenav/）
python3 .codenav/scripts/bsp_filter_gen.py \
  -b "<用户原始编译命令>" \
  --root $BSP_ROOT

wc -l .codenav/active_files.idx
```
```

- [ ] **Step 4: 改 codeindex SKILL.md 的 Phase 3.2**

把 gtags 输入文件路径从根改到 `.codenav/`：

```bash
grep -E '\.(c|h|cc|cpp|cxx|hpp|java|kt|S|s|asm)$' \
  .codenav/active_files.idx > .codenav/gtags.files
GTAGSLABEL=new-ctags gtags -v -f .codenav/gtags.files
```

- [ ] **Step 5: 验证脚本路径迁移不破坏功能**

部署到 atk（覆盖 Task 2 的旧部署）：

```bash
cd /home/leo/atk-rk3568_androidR_release_v1.4_20250104
mkdir -p .codenav/scripts
cp /home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts/_bsp_common.py .codenav/scripts/
cp /home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts/bsp_filter_gen.py .codenav/scripts/
cp /home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts/arg.sh .codenav/scripts/

python3 .codenav/scripts/bsp_filter_gen.py \
  -b "source build/envsetup.sh && lunch atk_rk3568_r-userdebug" \
  --root .

[ -f .codenav/active_files.idx ] && wc -l .codenav/active_files.idx
[ -f .codenav/active_files.idx.prev ] && echo "prev backup exists"
```

- [ ] **Step 6: Commit**

```bash
git add skills/androidbsp-codeindex-setup/SKILL.md \
        skills/androidbsp-codeindex-setup/scripts/bsp_filter_gen.py \
        skills/androidbsp-codeindex-setup/scripts/arg.sh
git commit -m "refactor(codeindex): migrate scripts and idx to .codenav/, deploy _bsp_common"
```

---

## Task 7: AGENTS.md 标记块注入工具 + 模板加版本

**Files:**
- Create: `skills/androidbsp-codeindex-setup/scripts/_inject_block.sh`
- Modify: `skills/androidbsp-codeindex-setup/assets/AGENTS.md.template`
- Modify: `skills/androidbsp-codecross-setup/assets/AGENTS.md.codecross.template`
- Modify: `skills/androidbsp-domaintrace-setup/assets/AGENTS.md.domaintrace.template`

- [ ] **Step 1: 写注入脚本**

Create `skills/androidbsp-codeindex-setup/scripts/_inject_block.sh`:

```bash
#!/usr/bin/env bash
# 把模板内容注入或更新到目标文件中的 BEGIN/END 标记块
# 用法: _inject_block.sh <marker> <template_file> <target_file>
# marker: 例如 "androidbsp-codeindex-setup"
# 模板文件首行必须是: <!-- BEGIN: <marker> v=<N> -->
# 末行必须是:        <!-- END: <marker> -->
set -euo pipefail
MARKER="${1:?marker}"
TEMPLATE="${2:?template path}"
TARGET="${3:?target path}"

[ -f "$TEMPLATE" ] || { echo "FAIL: template not found: $TEMPLATE" >&2; exit 1; }

NEW_VERSION=$(head -1 "$TEMPLATE" | grep -oE 'v=[0-9]+' | head -1 || echo "v=1")

if [ ! -f "$TARGET" ]; then
  cat "$TEMPLATE" > "$TARGET"
  echo "INFO: created $TARGET with $MARKER ($NEW_VERSION)"
  exit 0
fi

if grep -qF "BEGIN: $MARKER" "$TARGET"; then
  CUR_VERSION=$(grep -oE "BEGIN: $MARKER v=[0-9]+" "$TARGET" \
                | head -1 | grep -oE 'v=[0-9]+' || echo "v=0")
  if [ "$CUR_VERSION" = "$NEW_VERSION" ]; then
    echo "INFO: $MARKER already at $CUR_VERSION, skip"
    exit 0
  fi
  # 替换 BEGIN…END 之间（含两端）
  python3 - "$MARKER" "$TEMPLATE" "$TARGET" <<'PYEOF'
import re, sys
marker, tpl, tgt = sys.argv[1], sys.argv[2], sys.argv[3]
new_block = open(tpl).read().rstrip() + "\n"
content = open(tgt).read()
pat = re.compile(
    rf"<!-- BEGIN: {re.escape(marker)} v=\d+ -->.*?<!-- END: {re.escape(marker)} -->\n?",
    re.DOTALL
)
content = pat.sub(new_block, content)
open(tgt, "w").write(content)
PYEOF
  echo "INFO: updated $MARKER ($CUR_VERSION → $NEW_VERSION)"
else
  echo "" >> "$TARGET"
  cat "$TEMPLATE" >> "$TARGET"
  echo "INFO: appended $MARKER ($NEW_VERSION) to $TARGET"
fi
```

```bash
chmod +x skills/androidbsp-codeindex-setup/scripts/_inject_block.sh
```

- [ ] **Step 2: 测试注入脚本**

```bash
TMP=$(mktemp -d)
cat > $TMP/template.md <<'EOF'
<!-- BEGIN: test-marker v=1 -->
hello world
<!-- END: test-marker -->
EOF

# 第一次：append（无 target）
bash skills/androidbsp-codeindex-setup/scripts/_inject_block.sh \
  test-marker $TMP/template.md $TMP/target.md
grep -q "hello world" $TMP/target.md && echo "OK append"

# 第二次：跳过（同 v=1）
bash skills/androidbsp-codeindex-setup/scripts/_inject_block.sh \
  test-marker $TMP/template.md $TMP/target.md | grep -q "skip" && echo "OK skip"

# 升级模板
sed -i 's/v=1/v=2/; s/hello world/hello v2/' $TMP/template.md
bash skills/androidbsp-codeindex-setup/scripts/_inject_block.sh \
  test-marker $TMP/template.md $TMP/target.md | grep -q "1 → v=2"
grep -q "hello v2" $TMP/target.md && ! grep -q "hello world" $TMP/target.md \
  && echo "OK upgrade"

rm -rf $TMP
```
Expected: `OK append` / `OK skip` / `OK upgrade` 三行

- [ ] **Step 3: 改造 codeindex 主模板**

打开 `skills/androidbsp-codeindex-setup/assets/AGENTS.md.template`，第一行加：

```html
<!-- BEGIN: androidbsp-codeindex-setup v=1 -->
```

文件末尾加：

```html
<!-- END: androidbsp-codeindex-setup -->
```

- [ ] **Step 4: 改造 codecross 模板**

打开 `skills/androidbsp-codecross-setup/assets/AGENTS.md.codecross.template`。
- 检查首行已有 `<!-- BEGIN: androidbsp-codecross-setup -->` → 改成 `<!-- BEGIN: androidbsp-codecross-setup v=1 -->`
- 末行已有 `<!-- END: androidbsp-codecross-setup -->` → 不动

- [ ] **Step 5: 改造 domaintrace 模板**

打开 `skills/androidbsp-domaintrace-setup/assets/AGENTS.md.domaintrace.template`。
- 首行 `<!-- BEGIN: androidbsp-domaintrace-setup -->` → `<!-- BEGIN: androidbsp-domaintrace-setup v=1 -->`
- 末行不变

- [ ] **Step 6: 改 codeindex SKILL.md 的 Phase 5**

替换原 Phase 5 内容为：

```markdown
## Phase 5 — 注入 AGENTS.md 检索规则块

```bash
cd $BSP_ROOT

# 部署 _inject_block.sh（与公共库同位置）
cp $SKILL_DIR/scripts/_inject_block.sh .codenav/scripts/
chmod +x .codenav/scripts/_inject_block.sh

# 注入 codeindex 自己的标记块
.codenav/scripts/_inject_block.sh androidbsp-codeindex-setup \
  $SKILL_DIR/assets/AGENTS.md.template AGENTS.md

# CLAUDE.md 一行 import（保持原逻辑）
[ -f CLAUDE.md ] && grep -q '@AGENTS.md' CLAUDE.md || echo '@AGENTS.md' >> CLAUDE.md

# Cursor 入口（保持原逻辑）
mkdir -p .cursor/rules
cat > .cursor/rules/android-bsp.mdc <<'EOF'
---
alwaysApply: true
---
See @AGENTS.md for Android BSP code-search rules.
EOF
```

> 多个 skill 共享同一个 `_inject_block.sh`。codecross / domaintrace 的 setup 流程
> 直接调用同一脚本注入它们各自的标记块，互不破坏。
```

- [ ] **Step 7: 改 codecross SKILL.md 注入步骤**

打开 `skills/androidbsp-codecross-setup/SKILL.md`，找到 "注入 AGENTS.md 使用规则" 段落，替换为：

```bash
cd $BSP_ROOT
.codenav/scripts/_inject_block.sh androidbsp-codecross-setup \
  $SKILL_DIR/assets/AGENTS.md.codecross.template AGENTS.md
```

- [ ] **Step 8: 改 domaintrace SKILL.md 注入步骤**

打开 `skills/androidbsp-domaintrace-setup/SKILL.md`，同样改造（marker 用 `androidbsp-domaintrace-setup`）。

- [ ] **Step 9: Commit**

```bash
git add skills/androidbsp-codeindex-setup/scripts/_inject_block.sh \
        skills/androidbsp-codeindex-setup/SKILL.md \
        skills/androidbsp-codeindex-setup/assets/AGENTS.md.template \
        skills/androidbsp-codecross-setup/SKILL.md \
        skills/androidbsp-codecross-setup/assets/AGENTS.md.codecross.template \
        skills/androidbsp-domaintrace-setup/SKILL.md \
        skills/androidbsp-domaintrace-setup/assets/AGENTS.md.domaintrace.template
git commit -m "feat(agents-md): idempotent versioned block injection across 3 skills"
```

---

## Task 8: codecross / domaintrace 前置改成工件检测

**Files:**
- Modify: `skills/androidbsp-codecross-setup/SKILL.md`（前置段落）
- Modify: `skills/androidbsp-domaintrace-setup/SKILL.md`（前置段落）

- [ ] **Step 1: codecross 前置块替换**

打开 `skills/androidbsp-codecross-setup/SKILL.md`，把"前置要求"段的代码块替换为：

```bash
cd $BSP_ROOT
[ -f .codenav/scripts/_bsp_common.py ] \
  && [ -f compile_commands.json ] \
  && [ -f GTAGS ] \
  || { cat <<'EOF'
❌ 前置要求未满足：codeindex-setup 未完成部署。
缺少以下任一工件：.codenav/scripts/_bsp_common.py、compile_commands.json、GTAGS。
请先跑：
  /codeindex setup
EOF
  exit 1
}

# 验证公共库版本兼容
python3 -c "import sys; sys.path.insert(0,'.codenav/scripts'); \
  import _bsp_common as c; c.require_version('1.0.0')" \
  || { echo "FAIL: _bsp_common 版本过旧，请重跑 /codeindex setup 升级"; exit 1; }
```

- [ ] **Step 2: domaintrace 前置块替换**

`skills/androidbsp-domaintrace-setup/SKILL.md` 同样改造（与上面完全一致）。

- [ ] **Step 3: codecross / domaintrace 部署脚本路径改 .codenav/scripts/**

打开 `skills/androidbsp-codecross-setup/SKILL.md` 找 `cp $SKILL_DIR/scripts/...` 那段，把目标 `scripts/` 改为 `.codenav/scripts/`：

```bash
cd $BSP_ROOT
mkdir -p .codenav/scripts
cp $SKILL_DIR/scripts/jni_bridge.py     .codenav/scripts/
cp $SKILL_DIR/scripts/aidl_bridge.py    .codenav/scripts/
cp $SKILL_DIR/scripts/syscall_trace.py  .codenav/scripts/
cp $SKILL_DIR/scripts/ioctl_trace.py    .codenav/scripts/
cp $SKILL_DIR/scripts/xlang_find.py     .codenav/scripts/
chmod +x .codenav/scripts/*.py
```

domaintrace 同理（但目标是 13 个脚本 + 2 个新脚本 + dispatcher）。

- [ ] **Step 4: 冒烟段路径同步**

把每个 SKILL.md 的"冒烟验证"段里 `python3 scripts/<name>.py` 改为 `python3 .codenav/scripts/<name>.py`。

- [ ] **Step 5: Commit**

```bash
git add skills/androidbsp-codecross-setup/SKILL.md \
        skills/androidbsp-domaintrace-setup/SKILL.md
git commit -m "refactor(codecross,domaintrace): preflight on artifacts, deploy to .codenav/"
```

---

## Task 9: 重构 codecross 5 脚本切公共库

**Files:**
- Modify: `skills/androidbsp-codecross-setup/scripts/jni_bridge.py`
- Modify: `skills/androidbsp-codecross-setup/scripts/aidl_bridge.py`
- Modify: `skills/androidbsp-codecross-setup/scripts/syscall_trace.py`
- Modify: `skills/androidbsp-codecross-setup/scripts/ioctl_trace.py`
- Modify: `skills/androidbsp-codecross-setup/scripts/xlang_find.py`

**重构模式（每个脚本统一应用）**：

```python
# 旧：
import argparse, json, os, re, subprocess, sys
from pathlib import Path

def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def emit(tag, location, info=''):
    print(f'{tag}\t{location}\t{info}')

# 新：
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Finding, Emitter, make_parser, run_cmd, rg_find, gtags_lookup,
    find_bsp_root, parse_compile_commands, scan_partitions, first_existing,
    require_version,
)
require_version("1.0.0")
```

**main 函数模式**：

```python
# 旧：
ap = argparse.ArgumentParser(description='...')
ap.add_argument('--root', type=Path, default=Path.cwd())
ap.add_argument('symbol')
args = ap.parse_args()
# ... 业务逻辑直接 print emit(...) ...

# 新：
def main():
    p = make_parser('JNI bridge: Java/Kotlin native ↔ C JNI_*')
    p.add_argument('symbol')
    args = p.parse_args()

    with Emitter(args, Path(__file__).name) as e:
        # 业务逻辑替换 emit 调用为 e.emit(Finding(...), confidence='med',
        #                              source='static-rg', tags=['jni'])
        ...

if __name__ == '__main__':
    main()
```

- [ ] **Step 1: 重构 `xlang_find.py`（dispatcher，最简单）**

打开旧文件，整体替换为以下结构：

```python
#!/usr/bin/env python3
"""xlang_find.py — codecross dispatcher.
Heuristically routes a query to one of: jni_bridge / aidl_bridge /
syscall_trace / ioctl_trace based on symbol shape.
"""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import make_parser, require_version

require_version("1.0.0")

ROUTES = [
    # (regex, target script, brief)
    (re.compile(r'^[A-Z_]+_IO[RW]?C?\b|^_IO'), 'ioctl_trace.py', 'ioctl macro'),
    (re.compile(r'^__NR_'), 'syscall_trace.py', 'syscall NR'),
    (re.compile(r'^I[A-Z][A-Za-z0-9]+$'), 'aidl_bridge.py', 'AIDL/HIDL interface'),
    (re.compile(r'^Java_'), 'jni_bridge.py', 'JNI mangled symbol'),
    # default fallback by content sniffing later
]


def main():
    p = make_parser('codecross dispatcher')
    p.add_argument('symbol')
    p.add_argument('--force', choices=['jni', 'aidl', 'syscall', 'ioctl'],
                   help='bypass routing heuristic')
    args = p.parse_args()

    if args.force:
        target = f'{args.force}_bridge.py' if args.force in ('jni', 'aidl') \
                 else f'{args.force}_trace.py'
    else:
        target = None
        for pat, t, why in ROUTES:
            if pat.search(args.symbol):
                target = t
                print(f'INFO: routing to {t} ({why})', file=sys.stderr)
                break
        if not target:
            # default fallback: try syscall first (most common)
            target = 'syscall_trace.py'
            print(f'INFO: no clear route, defaulting to {target}',
                  file=sys.stderr)

    cmd = [sys.executable, str(Path(__file__).parent / target)]
    if args.json:
        cmd.append('--json')
    if args.no_events:
        cmd.append('--no-events')
    if args.root:
        cmd += ['--root', str(args.root)]
    cmd += ['--timeout', str(args.timeout), args.symbol]

    sys.exit(subprocess.call(cmd))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 重构 `syscall_trace.py`**

打开现有 `syscall_trace.py`。保留它的领域逻辑（找 `__NR_*`、内核 `SYSCALL_DEFINE*`、用户态 wrapper），但：

1. 顶部 import 块替换为公共库（见上面"重构模式"）
2. 删除本文件中重复的 `run()` / `emit()` 定义
3. argparse 替换为 `make_parser('syscall trace: userspace → __NR_* → kernel')`
4. 主逻辑包到 `with Emitter(args, 'syscall_trace.py') as e:` 内
5. 每个 `print(f'{tag}\t...')` 替换为：
   ```python
   e.emit(
       Finding(tag='USER-WRAPPER', file=path, line=lineno, snippet=snip),
       confidence='med', source='static-rg', tags=['syscall']
   )
   ```
6. 内部 rg/global 调用全部改用 `rg_find()` / `gtags_lookup()`

确保保留所有原有 tag（`USER-WRAPPER`、`KERNEL-ENTRY`、`NR-DEFINE` 等），不要改 tag 文本。

- [ ] **Step 3: 重构 `ioctl_trace.py`**

同 Step 2 模式：保留所有领域正则（`_IOR/_IOW/_IOWR` 解码、driver `case CMD:` 匹配），换 import / argparse / emit。tag 保持原文（`MACRO-DEF`、`DRIVER-CASE`、`USER-CALL` 等）。

- [ ] **Step 4: 重构 `jni_bridge.py`**

同模式。保留：
- Java 端 `native fun` / `external` 检测正则
- C 端 `Java_<class>_<method>` mangling 解析
- 保留 tag（`NATIVE-DECL`、`JNI-IMPL`、`REGISTER-NATIVES` 等）

- [ ] **Step 5: 重构 `aidl_bridge.py`**

同模式。保留：
- `.aidl` / `.hal` interface 解析
- Bn/Bp 生成代码定位
- 实现端 onTransact 查找
- tag（`AIDL-IFACE`、`HIDL-IFACE`、`BN-IMPL`、`BP-CALLER` 等）

⚠️ **AIDL 多 backend 改造延后到 Task 13**——本任务只做基础切换。

- [ ] **Step 6: 验证可执行 + 单元自检**

```bash
cd /tmp && for s in xlang_find jni_bridge aidl_bridge syscall_trace ioctl_trace; do
  python3 -c "
import sys
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-codecross-setup/scripts')
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts')
import importlib
m = importlib.import_module('$s')
print(f'$s OK')
"
done
```

Expected: 5 OK 行

- [ ] **Step 7: 行数对比**

```bash
wc -l skills/androidbsp-codecross-setup/scripts/*.py
```
Expected: 总行数比基线（之前 ~1500）下降 ~25%

- [ ] **Step 8: Commit**

```bash
git add skills/androidbsp-codecross-setup/scripts/
git commit -m "refactor(codecross): adopt _bsp_common Emitter and shared primitives"
```

---

## Task 10: 重构 domaintrace 14 脚本切公共库

**Files:**
- Modify: `skills/androidbsp-domaintrace-setup/scripts/binder_svc.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/build_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/dt_bind.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/firmware_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/initrc_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/kconfig_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/media_topo.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/netlink_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/prop_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/selinux_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/subsys_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/sysfs_attr.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/domain_find.py`

**应用 Task 9 完全相同的重构模式**到 13 个脚本 + 1 个 dispatcher。

- [ ] **Step 1: 重构 `domain_find.py`（dispatcher）**

参照 Task 9 Step 1 的 `xlang_find.py`，写 domain_find 的 dispatcher 路由表：

```python
ROUTES = [
    (re.compile(r','), 'dt_bind.py', 'DT compatible (vendor,part)'),
    (re.compile(r'^CONFIG_'), 'kconfig_trace.py', 'Kconfig symbol'),
    (re.compile(r'^ro\.|^persist\.|^sys\.|^vendor\.'), 'prop_trace.py', 'Android property'),
    (re.compile(r'^I[A-Z][A-Za-z0-9.]+$'), 'binder_svc.py', 'Binder interface'),
    (re.compile(r'^lib[A-Za-z0-9_.+-]+(\.so)?$'), 'build_trace.py', 'lib module'),
    (re.compile(r'^[a-z_]+_(probe|init|exit)$'), 'subsys_trace.py', 'driver entry'),
    (re.compile(r'^/sys/|^/proc/|^/d/'), 'sysfs_attr.py', 'sysfs/procfs path'),
    (re.compile(r'\.bin$|firmware'), 'firmware_trace.py', 'firmware blob'),
    (re.compile(r'^NL[A-Z]+|^GENL_'), 'netlink_trace.py', 'netlink family'),
    # tail fallback
]

# 同 xlang_find.py 的 main 结构
```

- [ ] **Step 2: 重构 `dt_bind.py`**

按 Task 9 模式。保留：
- DTS 文件枚举（kernel/arch/<arch>/boot/dts/、vendor DT 路径）
- compatible 双向匹配（DTS 出现 → driver `of_device_id`；driver 声明 → DTS 引用）
- DTBO overlay 解析
- tag：`DTS-COMPAT`、`DRV-MATCH`、`DTBO-OVERLAY` 等

- [ ] **Step 3: 重构 `sysfs_attr.py`**

保留 DEVICE_ATTR / DEVICE_ATTR_RW / DEVICE_ATTR_RO / DEVICE_ATTR_WO / BIN_ATTR 宏识别（**注意：BIN_ATTR / DEVICE_ATTR_RW 等是新增——本任务先确保现有正则迁移过来，新宏覆盖在 Task 11 加**）。

- [ ] **Step 4: 重构 `binder_svc.py`**

保留 service 注册检测（`addService` / `getService` / `IServiceManager`）+ HIDL/AIDL 混合 + VINTF manifest 解析。VINTF 多分区扫描在 Task 11 加。

- [ ] **Step 5: 重构 `selinux_trace.py`**

保留 te 文件解析、type/domain/class 提取、avc denied 反查规则。多分区 sepolicy 扩展放 Task 11。

- [ ] **Step 6: 重构 `subsys_trace.py`**

保留 clock / regulator / GPIO / IRQ / power-domain provider/consumer 双向追踪。

- [ ] **Step 7: 重构 `prop_trace.py`**

保留 property 读写双向（Java `SystemProperties.get/set` / native `__system_property_get/set`）+ build.prop 出处 + init.rc trigger。多分区 build.prop 扩展放 Task 11。

- [ ] **Step 8: 重构 `build_trace.py`**

保留 Android.bp / Android.mk 解析、模块 → 安装路径、VNDK 标记。aconfig / prefab / APEX 扩展放 Task 11 / Task 15。

- [ ] **Step 9: 重构 `initrc_trace.py`**

保留 trigger / action / service 三段链。多 init.rc 来源扩展放 Task 11。

- [ ] **Step 10: 重构 `kconfig_trace.py`**

保留 defconfig + Kconfig 定义 + #ifdef + Makefile 四元组追踪。GKI 多内核路径扩展放 Task 12。

- [ ] **Step 11: 重构 `firmware_trace.py`**

保留 `request_firmware` / `MODULE_DEVICE_TABLE` / 文件系统打包路径。GKI vendor_dlkm 扩展放 Task 12。

- [ ] **Step 12: 重构 `netlink_trace.py`**

保留 `genl_register_family` / userspace 使用追踪。

- [ ] **Step 13: 重构 `media_topo.py`**

保留 V4L2 subdev 注册 + pad link + DT port 静态拓扑。

- [ ] **Step 14: 验证全部脚本可 import**

```bash
cd /tmp && for s in domain_find dt_bind sysfs_attr binder_svc selinux_trace \
                    subsys_trace prop_trace build_trace initrc_trace \
                    kconfig_trace firmware_trace netlink_trace media_topo; do
  python3 -c "
import sys
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-domaintrace-setup/scripts')
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts')
import importlib
m = importlib.import_module('$s')
print(f'$s OK')
"
done
```
Expected: 13 OK 行

- [ ] **Step 15: 行数对比**

```bash
wc -l skills/androidbsp-domaintrace-setup/scripts/*.py
```
Expected: 总行数比基线（之前 ~3400）下降 ~25%

- [ ] **Step 16: Commit**

```bash
git add skills/androidbsp-domaintrace-setup/scripts/
git commit -m "refactor(domaintrace): adopt _bsp_common Emitter across 14 scripts"
```

---

## Task 11: 普适性补丁——多分区 / 多候选路径

**Files:**
- Modify: `skills/androidbsp-domaintrace-setup/scripts/selinux_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/prop_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/initrc_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/binder_svc.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/build_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/firmware_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/sysfs_attr.py`

每个脚本应用以下变更：

- [ ] **Step 1: selinux_trace 多分区 sepolicy + CIL + mapping**

在脚本里找已有 sepolicy 路径（如 `'system/sepolicy'` 字面量），替换为：

```python
sepolicy_dirs = scan_partitions(bsp_root, 'etc/selinux')
sepolicy_dirs += [
    bsp_root / 'system' / 'sepolicy',
    bsp_root / 'device',  # 兼容 vendor 自定义 device/<vendor>/sepolicy
]
sepolicy_dirs = [d for d in sepolicy_dirs if d.exists()]
if not sepolicy_dirs:
    e.emit(Finding(tag='WARN', file='-',
                   info={'msg': 'no sepolicy dir found in any partition'}),
           confidence='low')
    return
```

并把搜索的 glob 从只搜 `*.te` 扩展到 `['*.te', '*.cil']`，注意 mapping 文件 `mapping/<sdk>.cil` 单独识别（tag 用 `MAPPING-CIL`）。

- [ ] **Step 2: prop_trace 多分区 build.prop**

```python
prop_files = []
for part in scan_partitions(bsp_root, 'etc/build.prop'):
    prop_files.append(part)
# 兼容旧形式
for cand in [bsp_root / 'system/build.prop',
             bsp_root / 'vendor/build.prop',
             bsp_root / 'odm/etc/build.prop']:
    if cand.exists():
        prop_files.append(cand)
prop_files = list(set(prop_files))
```

- [ ] **Step 3: initrc_trace 6 处来源**

```python
init_dirs = []
for part in scan_partitions(bsp_root, 'etc/init'):
    init_dirs.append(part)
# 兼容旧的 init.rc 在 system/etc/init.rc 单文件位置
for cand in [bsp_root / 'system/core/rootdir/init.rc',
             bsp_root / 'init.rc']:
    if cand.exists():
        init_dirs.append(cand.parent)
# APEX init
for apex_init in (bsp_root / 'apex').glob('com.android.*/etc/init') \
        if (bsp_root / 'apex').exists() else []:
    init_dirs.append(apex_init)
```

- [ ] **Step 4: binder_svc VINTF 多分区**

```python
vintf_files = []
for part in PARTITIONS:
    for sub in ['etc/vintf/manifest.xml', 'manifest.xml']:
        cand = bsp_root / part / sub
        if cand.exists():
            vintf_files.append(cand)
# compat matrix 多版本
matrix_dir = bsp_root / 'hardware/interfaces/compatibility_matrices'
if matrix_dir.exists():
    matrices = list(matrix_dir.glob('compatibility_matrix.*.xml'))
```

- [ ] **Step 5: build_trace 多分区扫描**

把"安装路径推断"改成多分区候选：

```python
def _resolve_install_path(module: str, bsp_root: Path):
    candidates = []
    for part in PARTITIONS:
        for sub in [f'lib/{module}', f'lib64/{module}',
                    f'bin/{module}', f'etc/{module}']:
            p = bsp_root / part / sub
            if p.exists():
                candidates.append(p)
    return candidates
```

- [ ] **Step 6: firmware_trace 多 firmware 路径**

```python
fw_dirs = []
for part in scan_partitions(bsp_root, 'firmware'):
    fw_dirs.append(part)
for cand in [bsp_root / 'vendor/etc/firmware',
             bsp_root / 'odm/firmware',
             bsp_root / 'vendor_dlkm/firmware']:
    if cand.exists():
        fw_dirs.append(cand)
```

并加 modules.load / modules.blocklist 解析：

```python
modlist_files = []
for cand in [bsp_root / 'vendor/lib/modules/modules.load',
             bsp_root / 'vendor_dlkm/lib/modules/modules.load',
             bsp_root / 'odm/lib/modules/modules.load']:
    if cand.exists():
        modlist_files.append(cand)
        for ko in cand.read_text().splitlines():
            if ko.strip():
                e.emit(Finding(tag='MOD-LOAD', file=str(cand), line=0,
                               snippet=ko.strip(),
                               info={'partition': cand.parts[-3]}),
                       confidence='high', source='static-rg', tags=['kmod'])
```

- [ ] **Step 7: sysfs_attr 新宏家族**

把现有 `DEVICE_ATTR\(` 正则扩展为：

```python
SYSFS_PATTERNS = [
    r'\bDEVICE_ATTR(?:_RW|_RO|_WO|_ADMIN_RW|_ADMIN_RO)?\s*\(',
    r'\bBIN_ATTR(?:_RW|_RO|_WO)?\s*\(',
    r'\bSTATIC_DEVICE_ATTR\s*\(',
]
combined = '|'.join(SYSFS_PATTERNS)
hits = rg_find(combined, globs=['*.c', '*.h'])
```

- [ ] **Step 8: 测试在 atk 上各跑一条**

```bash
cd /home/leo/atk-rk3568_androidR_release_v1.4_20250104
# 部署最新版本（手动）
cp /home/leo/ai-config-hub/skills/androidbsp-domaintrace-setup/scripts/{selinux_trace,prop_trace,initrc_trace,binder_svc,build_trace,firmware_trace,sysfs_attr}.py .codenav/scripts/

python3 .codenav/scripts/prop_trace.py ro.product.model | head -5
python3 .codenav/scripts/firmware_trace.py rk_tb_8852be_fw.bin | head -10
python3 .codenav/scripts/binder_svc.py android.hardware.camera.provider | head -5
```
Expected: 每条非空，无 traceback

- [ ] **Step 9: Commit**

```bash
git add skills/androidbsp-domaintrace-setup/scripts/
git commit -m "feat(domaintrace): multi-partition probing for Android 11+ universal compat"
```

---

## Task 12: 普适性补丁——GKI 内核布局

**Files:**
- Modify: `skills/androidbsp-domaintrace-setup/scripts/kconfig_trace.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/firmware_trace.py`

- [ ] **Step 1: kconfig_trace 多内核根候选**

在脚本里找"kernel root"判断逻辑，替换为：

```python
def _kernel_roots(bsp_root: Path) -> list[Path]:
    candidates = [
        bsp_root / 'kernel' / 'common',         # GKI generic
        bsp_root / 'kernel' / 'private',        # GKI vendor
        bsp_root / 'kernel-5.10', bsp_root / 'kernel-5.15',
        bsp_root / 'kernel-6.1', bsp_root / 'kernel-6.6',
    ]
    # vendor-specific kernel paths
    for vendor in ['msm', 'rockchip', 'mediatek', 'spreadtrum', 'sprd']:
        for d in bsp_root.glob(f'kernel/{vendor}*'):
            candidates.append(d)
        for d in bsp_root.glob(f'vendor/{vendor}*/kernel*'):
            candidates.append(d)
    # legacy single kernel/
    candidates.append(bsp_root / 'kernel')
    return [d for d in candidates if d.exists() and (d / 'Kconfig').exists()]

kernel_roots = _kernel_roots(bsp_root)
if not kernel_roots:
    e.emit(Finding(tag='WARN', file='-',
                   info={'msg': 'no kernel root with Kconfig found'}),
           confidence='low')
    return
```

后续 defconfig / Kconfig 搜索都对每个 kernel root 跑一遍。

- [ ] **Step 2: firmware_trace kmod 加载多分区**

已在 Task 11 Step 6 完成 modules.load 部分。本步骤只需确认 vendor_dlkm 已加入 fw_dirs（Task 11 Step 6 已含）。

- [ ] **Step 3: 测试**

```bash
cp skills/androidbsp-domaintrace-setup/scripts/kconfig_trace.py \
   /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/

python3 /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/kconfig_trace.py \
  CONFIG_DRM_ROCKCHIP | head -10
```
Expected: 找到 defconfig 行 + Kconfig 定义 + 至少一处 `#ifdef`

- [ ] **Step 4: Commit**

```bash
git add skills/androidbsp-domaintrace-setup/scripts/kconfig_trace.py
git commit -m "feat(kconfig_trace): GKI multi-kernel-root candidate probing"
```

---

## Task 13: 普适性补丁——AIDL 多 backend + 版本

**Files:**
- Modify: `skills/androidbsp-codecross-setup/scripts/aidl_bridge.py`

- [ ] **Step 1: 加多 backend 生成路径识别**

在 aidl_bridge.py 中，添加：

```python
AIDL_BACKENDS = ['cpp', 'ndk', 'java', 'rust']

def _aidl_generated_paths(bsp_root: Path, iface: str) -> list[tuple[str, Path]]:
    """For each AIDL backend, find generated stub paths under out/soong."""
    out = bsp_root / 'out' / 'soong' / '.intermediates'
    if not out.exists():
        return []
    found = []
    for backend in AIDL_BACKENDS:
        # Soong intermediate naming convention: */<iface>-V<n>-<backend>-source/
        for p in out.rglob(f'{iface}-V*-{backend}-source'):
            found.append((backend, p))
        for p in out.rglob(f'{iface}-{backend}-source'):  # versionless
            found.append((backend, p))
    return found

# 主流程末尾追加：
for backend, path in _aidl_generated_paths(bsp_root, iface):
    e.emit(Finding(tag=f'GEN-{backend.upper()}', file=str(path), line=0,
                   snippet=f'AIDL {backend} backend stubs',
                   info={'backend': backend}),
           confidence='high', source='static-rg', tags=['aidl', backend])
```

- [ ] **Step 2: 加 stable interface 版本枚举**

```python
def _aidl_versions(bsp_root: Path, iface_path: Path) -> list[str]:
    """List V<n> dirs under aidl_api/<iface>/"""
    api_dir = iface_path.parent / 'aidl_api' / iface_path.stem
    if not api_dir.exists():
        return []
    return sorted(d.name for d in api_dir.iterdir() if d.name.startswith('V'))

# 当找到 .aidl 文件时：
for hit_file, hit_line, hit_snip in aidl_files:
    versions = _aidl_versions(bsp_root, Path(hit_file))
    if versions:
        e.emit(Finding(tag='AIDL-VERSIONS', file=hit_file, line=0,
                       snippet=f'stable versions: {", ".join(versions)}',
                       info={'versions': versions}),
               confidence='high', source='static-rg', tags=['aidl', 'stable'])
```

- [ ] **Step 3: 测试在 atk 上**

```bash
cp skills/androidbsp-codecross-setup/scripts/aidl_bridge.py \
   /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/

python3 /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/aidl_bridge.py \
  IRadio | head -20
```
Expected: 至少看到 AIDL 接口 + 至少一个 backend 的 GEN-* tag

- [ ] **Step 4: Commit**

```bash
git add skills/androidbsp-codecross-setup/scripts/aidl_bridge.py
git commit -m "feat(aidl_bridge): multi-backend (cpp/ndk/java/rust) + stable version enum"
```

---

## Task 14: 新脚本 `bootcfg_trace.py`

**Files:**
- Create: `skills/androidbsp-domaintrace-setup/scripts/bootcfg_trace.py`

- [ ] **Step 1: 写脚本**

Create `skills/androidbsp-domaintrace-setup/scripts/bootcfg_trace.py`:

```python
#!/usr/bin/env python3
"""bootcfg_trace.py — trace androidboot.* parameter sources.

In Android 11+, kernel cmdline + bootconfig (Android 12+) + DT chosen node
all contribute to androidboot.* params consumed by init/property service.
This script enumerates definitions and consumers for a given androidboot key.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, find_bsp_root, make_parser, rg_find, require_version,
    scan_partitions,
)

require_version("1.0.0")


def _bootconfig_files(bsp_root: Path) -> list[Path]:
    """Find vendor_boot bootconfig sources (Android 12+)."""
    found = []
    # Soong-generated bootconfig assembly inputs
    for cand in [bsp_root / 'device', bsp_root / 'vendor']:
        if cand.exists():
            for p in cand.rglob('bootconfig.txt'):
                found.append(p)
            for p in cand.rglob('vendor-bootconfig*'):
                found.append(p)
    return found


def _cmdline_sources(bsp_root: Path) -> list[Path]:
    """Find kernel cmdline sources (defconfig CMDLINE, BoardConfig, etc)."""
    found = []
    for cand in (bsp_root / 'device').rglob('BoardConfig*.mk') \
            if (bsp_root / 'device').exists() else []:
        found.append(cand)
    return found


def main():
    p = make_parser('Trace androidboot.<key> parameter sources & consumers.')
    p.add_argument('key', help='e.g. androidboot.serialno or just serialno')
    args = p.parse_args()

    key = args.key if args.key.startswith('androidboot.') \
                   else f'androidboot.{args.key}'

    try:
        bsp_root = Path(args.root) if args.root else find_bsp_root()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    with Emitter(args, Path(__file__).name) as em:
        # 1) bootconfig 文件中的定义
        for bc_file in _bootconfig_files(bsp_root):
            for hit in rg_find(re.escape(key), globs=['*'], root=bc_file.parent):
                if str(bc_file).endswith(hit[0].split('/')[-1]):
                    em.emit(Finding(tag='BOOTCFG-DEF', file=hit[0],
                                    line=hit[1], snippet=hit[2],
                                    info={'source': 'bootconfig'}),
                            confidence='high', source='static-rg',
                            tags=['bootcfg'])

        # 2) BoardConfig.mk 中的 CMDLINE 定义
        for bc_file in _cmdline_sources(bsp_root):
            for hit in rg_find(rf'CMDLINE.*{re.escape(key)}',
                               root=bc_file.parent):
                em.emit(Finding(tag='CMDLINE-DEF', file=hit[0],
                                line=hit[1], snippet=hit[2],
                                info={'source': 'kernel-cmdline'}),
                        confidence='high', source='static-rg',
                        tags=['bootcfg'])

        # 3) DT chosen node
        for dts_dir in [bsp_root / 'kernel/common/arch',
                        bsp_root / 'kernel/private',
                        bsp_root / 'kernel']:
            if not dts_dir.exists():
                continue
            for hit in rg_find(rf'bootargs\s*=.*{re.escape(key)}',
                               globs=['*.dts*'], root=dts_dir):
                em.emit(Finding(tag='DT-CHOSEN', file=hit[0],
                                line=hit[1], snippet=hit[2],
                                info={'source': 'dt-chosen'}),
                        confidence='med', source='static-rg', tags=['bootcfg'])

        # 4) 消费方：init / property_service / system_properties
        for hit in rg_find(re.escape(key),
                           globs=['*.cpp', '*.c', '*.rc', '*.java', '*.kt'],
                           root=bsp_root):
            em.emit(Finding(tag='CONSUMER', file=hit[0], line=hit[1],
                            snippet=hit[2], info={}),
                    confidence='med', source='static-rg', tags=['bootcfg'])


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 验证可执行**

```bash
python3 -c "
import sys
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts')
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-domaintrace-setup/scripts')
import bootcfg_trace
print('bootcfg_trace OK')
"

python3 skills/androidbsp-domaintrace-setup/scripts/bootcfg_trace.py --help
```

- [ ] **Step 3: atk 上跑一条**

```bash
cp skills/androidbsp-domaintrace-setup/scripts/bootcfg_trace.py \
   /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/

python3 /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/bootcfg_trace.py \
  serialno | head -20
```
Expected: 不崩；可能输出空（atk 是 Android R，bootconfig 是 Android 12+ 才标准化），但应有 CONSUMER tag 命中

- [ ] **Step 4: 加进 domaintrace 的 SKILL.md 部署清单**

打开 `skills/androidbsp-domaintrace-setup/SKILL.md`，"部署脚本" 段加：

```bash
cp $SKILL_DIR/scripts/bootcfg_trace.py    .codenav/scripts/
```

- [ ] **Step 5: Commit**

```bash
git add skills/androidbsp-domaintrace-setup/scripts/bootcfg_trace.py \
        skills/androidbsp-domaintrace-setup/SKILL.md
git commit -m "feat(domaintrace): add bootcfg_trace.py for androidboot.* sources"
```

---

## Task 15: 新脚本 `apex_locate.py` + build_trace 集成

**Files:**
- Create: `skills/androidbsp-domaintrace-setup/scripts/apex_locate.py`
- Modify: `skills/androidbsp-domaintrace-setup/scripts/build_trace.py`

- [ ] **Step 1: 写 apex_locate.py**

Create `skills/androidbsp-domaintrace-setup/scripts/apex_locate.py`:

```python
#!/usr/bin/env python3
"""apex_locate.py — locate APEX module definitions and contents.

Inputs: APEX module name (e.g. com.android.media) or library/binary name
        contained in an APEX.

Outputs:
  - APEX-DEF: Soong apex {} block (Android.bp)
  - APEX-MANIFEST: apex_manifest.json/.pb
  - APEX-INSTALL: /apex/<name>/ install path under out/
  - APEX-CONTENT: which APEX(es) contain a given lib/binary (if reverse query)
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, find_bsp_root, make_parser, rg_find, require_version,
)

require_version("1.0.0")


def _find_apex_blocks(bsp_root: Path, name: str):
    """Search Android.bp files for `apex { name: "<name>" }` blocks."""
    return rg_find(
        rf'apex\s*\{{[^}}]*name:\s*"{re.escape(name)}"',
        globs=['*.bp'], root=bsp_root,
        extra=['-U', '--multiline-dotall']
    )


def _find_apex_manifest(bsp_root: Path, name: str):
    """Locate apex_manifest.json or .pb for the given APEX."""
    found = []
    for ext in ['json', 'pb']:
        for p in bsp_root.rglob(f'apex_manifest.{ext}'):
            try:
                if ext == 'json':
                    data = json.loads(p.read_text())
                    if data.get('name') == name:
                        found.append((p, data))
                else:
                    # 二进制 .pb，用 strings 兜底
                    if name.encode() in p.read_bytes():
                        found.append((p, None))
            except Exception:
                continue
    return found


def _reverse_lookup_member(bsp_root: Path, member: str):
    """Find which APEX(es) declare `member` in their `native_shared_libs` /
    `binaries` / `prebuilts` lists."""
    return rg_find(
        rf'"{re.escape(member)}"',
        globs=['*.bp'], root=bsp_root,
    )


def main():
    p = make_parser('Locate APEX module definitions / contents.')
    p.add_argument('name',
                   help='APEX module name (com.android.X) or library/binary '
                        'to reverse-lookup which APEX contains it')
    p.add_argument('--reverse', action='store_true',
                   help='treat name as a member to find containing APEX')
    args = p.parse_args()

    try:
        bsp_root = Path(args.root) if args.root else find_bsp_root()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    with Emitter(args, Path(__file__).name) as em:
        if args.reverse:
            for hit in _reverse_lookup_member(bsp_root, args.name):
                em.emit(Finding(tag='APEX-CONTAINER', file=hit[0],
                                line=hit[1], snippet=hit[2],
                                info={'member': args.name}),
                        confidence='med', source='static-rg', tags=['apex'])
            return

        # forward lookup: APEX block + manifest + install path
        for hit in _find_apex_blocks(bsp_root, args.name):
            em.emit(Finding(tag='APEX-DEF', file=hit[0], line=hit[1],
                            snippet=hit[2][:200], info={}),
                    confidence='high', source='static-rg', tags=['apex'])

        for path, data in _find_apex_manifest(bsp_root, args.name):
            info = {'version': data.get('version')} if data else {}
            em.emit(Finding(tag='APEX-MANIFEST', file=str(path), line=0,
                            snippet=f'manifest for {args.name}', info=info),
                    confidence='high', source='static-rg', tags=['apex'])

        # install path
        install = bsp_root / 'out' / 'target' / 'product'
        if install.exists():
            for p in install.glob(f'*/system/apex/{args.name}.apex'):
                em.emit(Finding(tag='APEX-INSTALL', file=str(p), line=0,
                                snippet='built apex blob', info={}),
                        confidence='high', source='static-rg', tags=['apex'])


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: build_trace.py 加 APEX block 识别**

在 build_trace.py 中找到 `module_def` / Android.bp 解析逻辑，添加：

```python
# 在主流程中：
for hit in rg_find(rf'apex\s*\{{[^}}]*name:\s*"[^"]*{re.escape(module)}',
                   globs=['*.bp'], root=bsp_root,
                   extra=['-U', '--multiline-dotall']):
    e.emit(Finding(tag='APEX-OWNER', file=hit[0], line=hit[1],
                   snippet=hit[2][:200],
                   info={'note': 'use apex_locate.py for full APEX info'}),
           confidence='med', source='static-rg', tags=['apex'])
```

- [ ] **Step 3: 验证 + atk 测试**

```bash
python3 -c "
import sys
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts')
sys.path.insert(0, '/home/leo/ai-config-hub/skills/androidbsp-domaintrace-setup/scripts')
import apex_locate; print('apex_locate OK')
"

cp skills/androidbsp-domaintrace-setup/scripts/apex_locate.py \
   /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/

python3 /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/apex_locate.py \
  com.android.media | head -20
```
Expected: 至少看到 APEX-DEF 或 APEX-MANIFEST 命中（Android R 已有 APEX）

- [ ] **Step 4: 加进 SKILL.md 部署清单**

`skills/androidbsp-domaintrace-setup/SKILL.md` "部署脚本" 段加：

```bash
cp $SKILL_DIR/scripts/apex_locate.py      .codenav/scripts/
```

- [ ] **Step 5: Commit**

```bash
git add skills/androidbsp-domaintrace-setup/scripts/apex_locate.py \
        skills/androidbsp-domaintrace-setup/scripts/build_trace.py \
        skills/androidbsp-domaintrace-setup/SKILL.md
git commit -m "feat(domaintrace): add apex_locate.py and APEX-OWNER tag in build_trace"
```

---

## Task 16: AGENTS.md 模板加 events.jsonl + Kotlin/Rust 段

**Files:**
- Modify: `skills/androidbsp-codeindex-setup/assets/AGENTS.md.template`

- [ ] **Step 1: 加 "历史证据" 段**

在 `AGENTS.md.template` 的"按场景选工具"段后插入：

```markdown
## 历史证据（events.jsonl）

`.codenav/events.jsonl` 是过去所有静态查询结果的累积日志。每行一条 JSON event，
schema 为 `androidbsp.event/v1`。

**新查询前先看历史**：

```bash
tail -n 200 .codenav/events.jsonl | jq -c 'select(.tags[] == "<topic>")'
```

合并多条 event（同 file + line + tag）的优先级：
1. `confidence: high` > `med` > `low`
2. `source` 以 `runtime-` 开头优先于 `static-`
3. 同 source 同 confidence，最新 `ts` 优先

**关闭日志写入**：日常调试不想污染日志时，给脚本加 `--no-events`。
**强制 JSON 输出**：脚本加 `--json` 改为输出 JSONL（每行一条 Finding）。
```

- [ ] **Step 2: 加 "已知盲区" 段**

在 "工具能力矩阵" 后追加：

```markdown
## 已知盲区与降级策略

| 语言 | 索引覆盖 | 降级方式 |
|---|---|---|
| C / C++ | clangd 语义 + gtags 符号 | — |
| Java | gtags（universal-ctags） | — |
| Kotlin | **仅语法级**（无类型推断） | 跨文件类型问题 → rg 全文 + 手读源码 |
| Rust | **仅文本搜索**（无符号索引） | 任何 Rust 查询 → rg 为主 |
| AIDL/HIDL/DT/SELinux/Binder/etc. | 由 codecross / domaintrace 覆盖 | 见对应段落 |
```

- [ ] **Step 3: 模板首行 `v=1` → `v=2`（因为内容变了）**

```html
<!-- BEGIN: androidbsp-codeindex-setup v=2 -->
```

下次 `_inject_block.sh` 会自动把已部署的 v=1 替换为 v=2。

- [ ] **Step 4: Commit**

```bash
git add skills/androidbsp-codeindex-setup/assets/AGENTS.md.template
git commit -m "docs(agents-md): add events.jsonl protocol and known blind spots"
```

---

## Task 17: idx_diff.py + bsp_filter_gen.py 自动备份

**Files:**
- Create: `skills/androidbsp-codeindex-setup/scripts/idx_diff.py`
- bsp_filter_gen.py 的 prev 备份逻辑已在 Task 6 完成

- [ ] **Step 1: 写 idx_diff.py**

Create `skills/androidbsp-codeindex-setup/scripts/idx_diff.py`:

```python
#!/usr/bin/env python3
"""idx_diff.py — diff active_files.idx vs active_files.idx.prev,
group by subsystem (first two path segments)."""
from __future__ import annotations
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import find_bsp_root, make_parser, require_version

require_version("1.0.0")


def _bucket(path: str) -> str:
    parts = path.split('/', 2)
    return '/'.join(parts[:2]) if len(parts) >= 2 else path


def main():
    p = make_parser('Diff active_files.idx with previous version.')
    p.add_argument('--top', type=int, default=20,
                   help='show top N changed buckets (default 20)')
    args = p.parse_args()

    try:
        bsp_root = Path(args.root) if args.root else find_bsp_root()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    cur = bsp_root / '.codenav' / 'active_files.idx'
    prev = bsp_root / '.codenav' / 'active_files.idx.prev'

    if not cur.exists():
        print(f"FAIL: {cur} not found", file=sys.stderr)
        sys.exit(2)
    if not prev.exists():
        print("INFO: no previous index — nothing to diff", file=sys.stderr)
        sys.exit(0)

    cur_set = set(cur.read_text().splitlines())
    prev_set = set(prev.read_text().splitlines())

    added = cur_set - prev_set
    removed = prev_set - cur_set

    print(f"=== changes ({len(added)} added, {len(removed)} removed) ===")

    # bucket aggregation
    bucket_changes = defaultdict(lambda: [0, 0])  # [added, removed]
    for f in added:
        bucket_changes[_bucket(f)][0] += 1
    for f in removed:
        bucket_changes[_bucket(f)][1] += 1

    sorted_buckets = sorted(bucket_changes.items(),
                            key=lambda x: -(x[1][0] + x[1][1]))

    print(f"\n=== top {args.top} subsystems by change ===")
    for bucket, (a, r) in sorted_buckets[:args.top]:
        sign = '+' if a > r else '-' if r > a else '='
        print(f"  {sign} {bucket:40s}  +{a:5d} / -{r:5d}")

    if args.json:
        import json as _json
        out = {
            'added_count': len(added),
            'removed_count': len(removed),
            'buckets': [{'bucket': b, 'added': a, 'removed': r}
                        for b, (a, r) in sorted_buckets],
        }
        print(_json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 测试**

```bash
# 在 atk 上模拟一次（假设 prev 已存在或 Task 6 已生成）
cd /home/leo/atk-rk3568_androidR_release_v1.4_20250104
[ -f .codenav/active_files.idx.prev ] || cp .codenav/active_files.idx .codenav/active_files.idx.prev

# 制造一些 diff
echo "fake/added/file.c" >> .codenav/active_files.idx
sed -i '1d' .codenav/active_files.idx.prev  # 删一行模拟"removed"

cp /home/leo/ai-config-hub/skills/androidbsp-codeindex-setup/scripts/idx_diff.py .codenav/scripts/
python3 .codenav/scripts/idx_diff.py
```
Expected: 输出含 added/removed 计数和 top buckets

- [ ] **Step 3: 加进 codeindex SKILL.md 部署清单**

`skills/androidbsp-codeindex-setup/SKILL.md` Phase 2 已有部署，确认 `idx_diff.py` 在 `cp` 列表里。

- [ ] **Step 4: Commit**

```bash
git add skills/androidbsp-codeindex-setup/scripts/idx_diff.py
git commit -m "feat(codeindex): add idx_diff.py for cross-target subsystem comparison"
```

---

## Task 18: codeindex Phase 4 加 Kotlin/Rust INFO

**Files:**
- Modify: `skills/androidbsp-codeindex-setup/SKILL.md`（Phase 4）

- [ ] **Step 1: 改 Phase 4 冒烟段**

打开 `skills/androidbsp-codeindex-setup/SKILL.md` 的 Phase 4，在末尾追加：

```bash
# 5. Kotlin / Rust 覆盖告知（仅 INFO）
RS_COUNT=$(grep -c '\.rs$' .codenav/active_files.idx 2>/dev/null || echo 0)
KT_COUNT=$(grep -c '\.kt$' .codenav/active_files.idx 2>/dev/null || echo 0)
if [ "$RS_COUNT" -gt 100 ]; then
  echo "INFO: Rust 文件 $RS_COUNT 个 — 索引为纯文本搜索（无符号索引），rg 为主"
fi
if [ "$KT_COUNT" -gt 100 ]; then
  echo "INFO: Kotlin 文件 $KT_COUNT 个 — gtags 仅做语法级（无类型推断），跨文件类型查询请降级到 rg + 手读"
fi
```

- [ ] **Step 2: Commit**

```bash
git add skills/androidbsp-codeindex-setup/SKILL.md
git commit -m "feat(codeindex): Phase 4 INFO about Kotlin/Rust coverage limits"
```

---

## Task 19: 三 SKILL.md 互相 "下一步" 指引 + meta-skill

**Files:**
- Modify: `skills/androidbsp-codeindex-setup/SKILL.md`
- Modify: `skills/androidbsp-codecross-setup/SKILL.md`
- Modify: `skills/androidbsp-domaintrace-setup/SKILL.md`
- Create: `skills/androidbsp-codenav/SKILL.md`
- Create: `skills/androidbsp-codenav/evals/evals.json`

- [ ] **Step 1: codeindex Phase 5 末尾加 "下一步"**

打开 `skills/androidbsp-codeindex-setup/SKILL.md`，在 Phase 5 末尾加：

```markdown
---

## 下一步（可选）

完整 BSP code-nav 还有两个补充层：

- `/code-cross setup` — 部署符号编码跨边界追踪（JNI / AIDL / HIDL / syscall / ioctl）
- `/domaintrace setup` — 部署领域知识多步追踪（DT / sysfs / Binder / SELinux / etc.）

或一步到位：

- `/codenav setup-all` — 元命令，依次跑 codeindex + codecross + domaintrace
```

- [ ] **Step 2: codecross 末尾加 "姐妹 skill"**

打开 `skills/androidbsp-codecross-setup/SKILL.md`，在文件末尾加：

```markdown
---

## 姐妹 skill

- `/domaintrace setup` — 领域知识多步追踪（rg 能搜到但需要领域知识串联多步）
- `/codenav setup-all` — 一步到位完整部署
```

- [ ] **Step 3: domaintrace 末尾加 "姐妹 skill" + runtime 接入预告**

打开 `skills/androidbsp-domaintrace-setup/SKILL.md`，文件末尾加：

```markdown
---

## 姐妹 skill

- `/code-cross setup` — 符号编码跨边界（JNI / AIDL / HIDL / syscall / ioctl）
- `/codenav setup-all` — 一步到位完整部署

## 未来：runtime-trace skill 接入

本 skill 部署的脚本默认向 `.codenav/events.jsonl` 追加 event。未来 runtime-trace
skill 会向同一文件追加 `runtime-ftrace` / `runtime-bpftrace` / `runtime-logcat` /
`runtime-dmesg` 等 source 的 event，AI agent 读取时按 schema `androidbsp.event/v1`
统一合并。本 skill 输出**已经满足这一契约**，无需改动。
```

- [ ] **Step 4: 创建 meta-skill 目录与 SKILL.md**

```bash
mkdir -p skills/androidbsp-codenav/evals
```

Create `skills/androidbsp-codenav/SKILL.md`:

```markdown
---
name: androidbsp-codenav
description: 'Android BSP 代码导航元 skill：编排 codeindex / codecross /
  domaintrace 三个 setup skill 的完整部署。当用户说「全套部署 codenav」
  「一次性配置 BSP 代码导航」「/codenav setup-all」「BSP code nav setup」时使用。'
command: /codenav
args:
  - name: setup-all
    description: '依次部署 codeindex → codecross → domaintrace 全套 code-nav'
  - name: status
    description: '（预留，本版未实现）报告 BSP 当前 codenav 部署状态'
---

# androidbsp-codenav

**职责单一**：编排现有三个 setup skill 完成完整 BSP code-nav 部署。
**不**做实际部署工作——所有真实工作交给三个 skill。

## /codenav setup-all

依次以"前一步成功才进下一步"的方式触发：

1. **androidbsp-codeindex-setup** 的 `setup` 流程
   （工具链 + active_files.idx + compdb + gtags + clangd + AGENTS.md 注入 +
   `_bsp_common.py` 部署）
2. **androidbsp-codecross-setup** 的 `setup` 流程
   （JNI / AIDL / HIDL / syscall / ioctl 脚本部署 + AGENTS.md 注入）
3. **androidbsp-domaintrace-setup** 的 `setup` 流程
   （DT / sysfs / Binder / SELinux / 子系统 / Property / Build / init.rc /
   Kconfig / Firmware / Netlink / V4L2 / bootcfg / APEX 14+ 个领域脚本 +
   AGENTS.md 注入）

任一步失败立即停止，输出失败原因。三个 setup skill 仍可独立调用。

## 执行模式

AI agent 在收到 `/codenav setup-all` 时应：

1. 先按 `androidbsp-codeindex-setup` SKILL.md 的全部 Phase 跑一遍
2. 完成且通过 Phase 4 冒烟后，按 `androidbsp-codecross-setup` SKILL.md 跑一遍
3. 完成且通过冒烟后，按 `androidbsp-domaintrace-setup` SKILL.md 跑一遍
4. 全部完成后输出汇总：

```
✅ codenav 部署完成
   - codeindex: <gtags 行数> / compdb <entries>
   - codecross: 5 个跨边界脚本就位
   - domaintrace: 14 个领域脚本就位
   - AGENTS.md: 3 段标记块齐全
   - .codenav/events.jsonl: 待 AI 使用时累积
```

## /codenav status

**预留命令，本版未实现。** 未来报告：deployment 状态、`_bsp_common` 版本、
AGENTS.md 三段齐全性、events.jsonl 大小 / 最近 N 条。

---

## 目录速查

```
skills/androidbsp-codenav/
├── SKILL.md      # 本文件（仅编排说明）
└── evals/evals.json
```

无 `scripts/` 或 `assets/`——本 skill 不部署任何文件，只调度别的 skill。
```

- [ ] **Step 5: 写 meta-skill evals.json**

Create `skills/androidbsp-codenav/evals/evals.json`:

```json
{
  "skill_name": "androidbsp-codenav",
  "evals": [
    {
      "id": 1,
      "prompt": "/codenav setup-all source build/envsetup.sh && lunch atk_rk3568_r-userdebug && ./build.sh -UKAp",
      "expected_output": "依次跑 codeindex setup → codecross setup → domaintrace setup 三个 skill；任一冒烟失败即停",
      "files": [],
      "assertions": [
        {"text": "首先调起 androidbsp-codeindex-setup 完成 Phase 1-5", "type": "contains_concept"},
        {"text": "codeindex 冒烟通过后才调 androidbsp-codecross-setup", "type": "structure"},
        {"text": "codecross 冒烟通过后才调 androidbsp-domaintrace-setup", "type": "structure"},
        {"text": "全程不重复部署 _bsp_common.py（codecross/domaintrace 检测它已就位）", "type": "no_duplicate"},
        {"text": "三 skill 各自向 AGENTS.md 注入自己的标记块，互不破坏", "type": "contains_concept"}
      ]
    },
    {
      "id": 2,
      "prompt": "/codenav setup-all 但 codeindex Phase 4 冒烟失败（compdb 为空）",
      "expected_output": "立即停止，输出 codeindex 失败原因；不进入 codecross / domaintrace",
      "files": [],
      "assertions": [
        {"text": "stop 在 codeindex 阶段", "type": "structure"},
        {"text": "明确告知是 compdb 失败而非别的", "type": "contains_concept"},
        {"text": "未触发 codecross 或 domaintrace 的部署", "type": "no_action"}
      ]
    }
  ]
}
```

- [ ] **Step 6: Commit**

```bash
git add skills/androidbsp-codeindex-setup/SKILL.md \
        skills/androidbsp-codecross-setup/SKILL.md \
        skills/androidbsp-domaintrace-setup/SKILL.md \
        skills/androidbsp-codenav/
git commit -m "feat(codenav): introduce meta-skill orchestrator + cross-skill pointers"
```

---

## Task 20: 写 compare_baseline.py

**Files:**
- Create: `skills/_validation/compare_baseline.py`

- [ ] **Step 1: 写脚本**

Create `skills/_validation/compare_baseline.py`:

```python
#!/usr/bin/env python3
"""compare_baseline.py — diff Phase 0 baseline vs Phase N rerun for atk validation.

Each baseline file (e.g. baseline_atk/01.txt) has format:
    === ID: 01
    === DESC: ...
    === CMD: ...
    === START: ...
    === EXIT: 0
    === ELAPSED: 1.23s
    === STDERR: <stderr lines>
    === STDOUT:
    <actual output>

This tool compares ID-by-ID: line counts, exit codes, elapsed time,
and reports each as ✅ unchanged / ⚠️ improved / ❌ regressed.
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path
from typing import Optional


def parse_run(path: Path) -> dict:
    text = path.read_text()
    sections = {}
    cur_key = None
    cur_lines = []
    for ln in text.splitlines():
        m = re.match(r'^=== ([A-Z]+):(.*)$', ln)
        if m:
            if cur_key is not None:
                sections[cur_key] = '\n'.join(cur_lines).strip()
            cur_key = m.group(1)
            cur_lines = [m.group(2).strip()] if m.group(2).strip() else []
        else:
            if cur_key is not None:
                cur_lines.append(ln)
    if cur_key is not None:
        sections[cur_key] = '\n'.join(cur_lines).strip()

    stdout = sections.get('STDOUT', '')
    return {
        'id': sections.get('ID', path.stem),
        'desc': sections.get('DESC', ''),
        'cmd': sections.get('CMD', ''),
        'exit': int(sections.get('EXIT', '0') or '0'),
        'elapsed': _parse_elapsed(sections.get('ELAPSED', '0s')),
        'stderr': sections.get('STDERR', ''),
        'stdout': stdout,
        'stdout_lines': len([l for l in stdout.splitlines() if l.strip()]),
    }


def _parse_elapsed(s: str) -> float:
    m = re.match(r'([\d.]+)', s)
    return float(m.group(1)) if m else 0.0


def classify(before: dict, after: dict) -> tuple[str, str]:
    """Return (status, reason). status in {unchanged, improved, regressed}."""
    if before['exit'] == 0 and after['exit'] != 0:
        return 'regressed', f'exit {before["exit"]} → {after["exit"]}'
    if before['exit'] != 0 and after['exit'] == 0:
        return 'improved', f'exit {before["exit"]} → 0'

    bl, al = before['stdout_lines'], after['stdout_lines']
    if bl == 0 and al > 0:
        return 'improved', f'lines 0 → {al} (was empty, now hits)'
    if bl > 0 and al == 0:
        return 'regressed', f'lines {bl} → 0 (lost all hits)'
    if bl == 0 and al == 0:
        return 'unchanged', 'both empty'

    delta_pct = (al - bl) / bl if bl else 0
    if abs(delta_pct) <= 0.10:
        return 'unchanged', f'lines {bl}→{al} (within 10%)'
    if delta_pct > 0:
        return 'improved', f'lines {bl}→{al} (+{int(delta_pct*100)}%)'
    return 'regressed', f'lines {bl}→{al} ({int(delta_pct*100)}%)'


def main():
    ap = argparse.ArgumentParser(description='Compare baseline vs rerun outputs.')
    ap.add_argument('--before', type=Path, required=True,
                    help='baseline dir (e.g. skills/_validation/baseline_atk/)')
    ap.add_argument('--after', type=Path, required=True,
                    help='rerun dir (e.g. skills/_validation/run_2026-04-N/)')
    ap.add_argument('--verbose', '-v', action='store_true',
                    help='show stdout diff for changed queries')
    args = ap.parse_args()

    before_files = {f.stem: f for f in args.before.glob('*.txt')
                    if f.stem != 'PHASE0_NOTES'}
    after_files = {f.stem: f for f in args.after.glob('*.txt')}

    common = sorted(set(before_files) & set(after_files))
    only_before = sorted(set(before_files) - set(after_files))
    only_after = sorted(set(after_files) - set(before_files))

    counts = {'unchanged': 0, 'improved': 0, 'regressed': 0}
    rows = []
    for qid in common:
        b = parse_run(before_files[qid])
        a = parse_run(after_files[qid])
        status, reason = classify(b, a)
        counts[status] += 1
        rows.append((qid, status, reason, b, a))

    print(f"=== {len(common)} queries compared ===")
    print(f"✅  {counts['unchanged']:3d} unchanged")
    print(f"⚠️   {counts['improved']:3d} improved")
    print(f"❌  {counts['regressed']:3d} regressed")
    if only_before:
        print(f"\n⚠️  only in baseline: {only_before}")
    if only_after:
        print(f"\n⚠️  only in rerun: {only_after}")

    if counts['regressed'] > 0:
        print("\n=== regressions ===")
        for qid, status, reason, b, a in rows:
            if status == 'regressed':
                print(f"\n  [{qid}] {b['desc']}")
                print(f"    CMD: {b['cmd']}")
                print(f"    {reason}")
                print(f"    elapsed: {b['elapsed']:.2f}s → {a['elapsed']:.2f}s")
                if args.verbose:
                    print("    --- before stdout (first 5) ---")
                    for ln in b['stdout'].splitlines()[:5]:
                        print(f"      {ln}")
                    print("    --- after stdout (first 5) ---")
                    for ln in a['stdout'].splitlines()[:5]:
                        print(f"      {ln}")

    print("\n=== improvements ===")
    for qid, status, reason, b, a in rows:
        if status == 'improved':
            print(f"  [{qid}] {b['desc']}: {reason}")

    # exit non-zero if any regression
    raise SystemExit(0 if counts['regressed'] == 0 else 1)


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 自检（用同一个目录当 before/after 应得 0 regression）**

```bash
chmod +x skills/_validation/compare_baseline.py
python3 skills/_validation/compare_baseline.py \
  --before skills/_validation/baseline_atk \
  --after  skills/_validation/baseline_atk
```
Expected: `✅ 24 unchanged`, exit 0

- [ ] **Step 3: Commit**

```bash
git add skills/_validation/compare_baseline.py
git commit -m "test: add compare_baseline.py for Phase 0/N regression detection"
```

---

## Task 21: Phase N · 重新部署 + 跑同样 24 条 + 对比

**Files:** 不改本仓库；只在 atk 上重部署并跑测试

- [ ] **Step 1: 清掉 atk 上的旧 .codenav 部署（保留索引）**

```bash
cd /home/leo/atk-rk3568_androidR_release_v1.4_20250104
# 保留索引文件（compdb / GTAGS / .clangd / active_files.idx），只清旧脚本
rm -rf .codenav/scripts
# 也清掉旧的根目录 scripts/（Task 2 部署到根的，迁移到 .codenav/ 后这里不再需要）
# 注意：BSP 自己的 scripts/ 不能动；只删我们部署的脚本
for f in bsp_filter_gen.py arg.sh jni_bridge.py aidl_bridge.py syscall_trace.py \
         ioctl_trace.py xlang_find.py dt_bind.py sysfs_attr.py binder_svc.py \
         selinux_trace.py subsys_trace.py prop_trace.py build_trace.py \
         initrc_trace.py kconfig_trace.py firmware_trace.py netlink_trace.py \
         media_topo.py domain_find.py; do
  rm -f scripts/$f
done

# 备份 AGENTS.md（Phase N 验证完后可对比注入是否幂等）
cp AGENTS.md AGENTS.md.phase0_backup
```

- [ ] **Step 2: 走 `/codenav setup-all`**

按照 `skills/androidbsp-codenav/SKILL.md` 的描述顺序执行：

```bash
SKILL_DIR=/home/leo/ai-config-hub/skills

# 1) codeindex
bash -c "
  cd /home/leo/atk-rk3568_androidR_release_v1.4_20250104
  source build/envsetup.sh
  # 跟着 skills/androidbsp-codeindex-setup/SKILL.md 跑 Phase 1-5
"
# (实际执行时由 AI agent 按 SKILL.md 逐步操作)

# 2) codecross
# 3) domaintrace

# 完成后验证：
ls /home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/scripts/*.py | wc -l
# Expected: 22+ 个 .py（_bsp_common + 18 旧 + bootcfg_trace + apex_locate + idx_diff）

grep -c "BEGIN: androidbsp-codeindex-setup" \
  /home/leo/atk-rk3568_androidR_release_v1.4_20250104/AGENTS.md
grep -c "BEGIN: androidbsp-codecross-setup" \
  /home/leo/atk-rk3568_androidR_release_v1.4_20250104/AGENTS.md
grep -c "BEGIN: androidbsp-domaintrace-setup" \
  /home/leo/atk-rk3568_androidR_release_v1.4_20250104/AGENTS.md
# Expected: 各 1
```

- [ ] **Step 3: 跑 24 条基线（同样的 query，新部署）**

```bash
mkdir -p skills/_validation/run_$(date +%F)

# 改 run_baseline.sh 调用：路径从 scripts/ 改为 .codenav/scripts/
# 临时方案：sed 替换
sed 's|scripts/|.codenav/scripts/|g' skills/_validation/run_baseline.sh \
  > /tmp/run_phase_n.sh
chmod +x /tmp/run_phase_n.sh

bash /tmp/run_phase_n.sh \
  /home/leo/atk-rk3568_androidR_release_v1.4_20250104 \
  skills/_validation/run_$(date +%F)
```

⚠️ **注意**：Task 3 的 `run_baseline.sh` 假设脚本在 `$BSP_ROOT/scripts/`。Phase N 时脚本在 `$BSP_ROOT/.codenav/scripts/`。所以这一步用临时 sed 修改一个版本来跑。**或者**改进 `run_baseline.sh` 加 `--script-dir` 参数（推荐）：

修改 `skills/_validation/run_baseline.sh` 加参数支持：

```bash
# 在 ARG 解析处加
SCRIPT_DIR="${3:-scripts}"  # 默认 scripts，phase N 传 .codenav/scripts
```

并把所有 `scripts/<x>.py` 改为 `${SCRIPT_DIR}/<x>.py`。

调用变成：
```bash
bash skills/_validation/run_baseline.sh \
  /home/leo/atk-rk3568_androidR_release_v1.4_20250104 \
  skills/_validation/run_$(date +%F) \
  .codenav/scripts
```

- [ ] **Step 4: 自动 diff**

```bash
python3 skills/_validation/compare_baseline.py \
  --before skills/_validation/baseline_atk \
  --after  skills/_validation/run_$(date +%F) \
  --verbose
```

**通过条件**：
- ❌ regressed = 0
- ⚠️ improved ≥ 3
- ✅ unchanged 占多数

**不通过则**：
- 把 regressed 的 query 列出
- 对每个回归手动 diff 输出
- 修脚本（多半是 Task 9-15 引入的逻辑漏写）
- 重跑 Phase N 直到通过

- [ ] **Step 5: 验证 events.jsonl 内容**

```bash
EVENTS=/home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/events.jsonl
[ -f "$EVENTS" ] && wc -l "$EVENTS"
head -3 "$EVENTS" | python3 -c "
import json, sys
for line in sys.stdin:
    rec = json.loads(line)
    assert rec['schema'] == 'androidbsp.event/v1', rec
    assert 'source' in rec and 'finding' in rec
    print('OK:', rec['source'], rec['script'])
"
```

- [ ] **Step 6: 用 jsonschema 验证 schema**

```bash
pip install --user jsonschema
python3 - <<'PYEOF'
import json
import sys
from jsonschema import validate

EVENT_SCHEMA = {
    "type": "object",
    "required": ["schema", "ts", "source", "script", "script_version",
                 "query", "finding", "confidence"],
    "properties": {
        "schema": {"const": "androidbsp.event/v1"},
        "ts": {"type": "string"},
        "source": {"type": "string", "pattern": "^(static-|runtime-|manual)"},
        "script": {"type": "string"},
        "script_version": {"type": "string"},
        "query": {"type": "object"},
        "finding": {"type": "object", "required": ["tag", "file"]},
        "confidence": {"enum": ["low", "med", "high"]},
        "tags": {"type": "array"},
    },
}

path = "/home/leo/atk-rk3568_androidR_release_v1.4_20250104/.codenav/events.jsonl"
with open(path) as f:
    for i, line in enumerate(f, 1):
        validate(json.loads(line), EVENT_SCHEMA)
print(f"OK: {i} events validated against schema")
PYEOF
```

**No commit**（Phase N 是验证步骤；只有 `run_baseline.sh` 改进入库）

- [ ] **Step 7: 提交 run_baseline.sh 的改进**

```bash
git add skills/_validation/run_baseline.sh
git commit -m "test: run_baseline.sh accepts --script-dir for Phase N runs"
```

---

## Task 22: 沉淀基线到每 skill 的 evals.json

**Files:**
- Modify: `skills/androidbsp-codeindex-setup/evals/evals.json`
- Modify: `skills/androidbsp-codecross-setup/evals/evals.json`
- Modify: `skills/androidbsp-domaintrace-setup/evals/evals.json`

- [ ] **Step 1: codeindex evals 增补**

打开 `skills/androidbsp-codeindex-setup/evals/evals.json`，加一条：

```json
{
  "id": 5,
  "prompt": "在 atk 上跑完 /codeindex setup 后，验证 .codenav/ 布局正确",
  "expected_output": ".codenav/scripts/_bsp_common.py 存在；.codenav/active_files.idx 行数与 baseline 相近；compile_commands.json 与 GTAGS 在根目录",
  "files": [],
  "assertions": [
    {"text": "_bsp_common.py 由 codeindex Phase 2 部署到 .codenav/scripts/", "type": "contains_concept"},
    {"text": "active_files.idx 在 .codenav/ 而不是根目录", "type": "structure"},
    {"text": "compile_commands.json / GTAGS 仍在根目录（工具硬性要求）", "type": "structure"},
    {"text": "AGENTS.md 中存在 BEGIN: androidbsp-codeindex-setup v=2 标记块", "type": "contains_concept"}
  ]
}
```

- [ ] **Step 2: codecross evals 增补**

把基线集中 codecross 相关的 5 条（query #4-#8、#23）作为期望命中数沉淀：

```json
[
  {
    "id": 3,
    "prompt": "在 atk 上跑 python3 .codenav/scripts/aidl_bridge.py IRadio",
    "expected_output": "至少 3 条命中（AIDL 接口定义 + 多 backend 生成路径）",
    "assertions": [
      {"text": "至少识别一个 AIDL backend（cpp/ndk/java/rust 之一）", "type": "contains_concept"},
      {"text": "events.jsonl 中有对应 source=static-rg 的 event 记录", "type": "contains_concept"},
      {"text": "覆盖 Android 11+ 的 AIDL 多 backend 演进", "type": "contains_concept"}
    ]
  },
  {
    "id": 4,
    "prompt": "在 atk 上跑 python3 .codenav/scripts/syscall_trace.py openat",
    "expected_output": "USER-WRAPPER + KERNEL-ENTRY 至少各 1 条",
    "assertions": [
      {"text": "userspace bionic / libc 实现命中", "type": "contains_concept"},
      {"text": "kernel SYSCALL_DEFINE 命中", "type": "contains_concept"}
    ]
  }
]
```

放入 `skills/androidbsp-codecross-setup/evals/evals.json` 的 `evals` 数组（保留原有条目）。

- [ ] **Step 3: domaintrace evals 增补**

加 5-6 条对应基线中 domaintrace 脚本的查询（#9-#22 选有代表性的）。结构同上。

- [ ] **Step 4: Commit**

```bash
git add skills/androidbsp-codeindex-setup/evals/evals.json \
        skills/androidbsp-codecross-setup/evals/evals.json \
        skills/androidbsp-domaintrace-setup/evals/evals.json
git commit -m "test: sediment Phase N validated baselines into per-skill evals"
```

---

## 完成检查表

按 spec 第 12 节验收标准对照：

- [ ] 三 skill 重命名完成（`grep -rn alltrace skills/ docs/` 零命中）
- [ ] `_bsp_common.py` 部署到 atk 的 `.codenav/scripts/`，所有脚本 import 成功
- [ ] `/codenav setup-all` 在干净 atk 上可一次性走通
- [ ] 三 skill 对 AGENTS.md 用标记块注入，互不破坏
- [ ] 24 条基线 Phase N vs Phase 0 自动 diff：`compare_baseline.py` 报告 0 项 ❌，≥ 3 项 ⚠️
- [ ] events.jsonl 在 atk 上有数据，每条 event 通过 `jsonschema` 校验
- [ ] `idx_diff.py`、`bootcfg_trace.py`、`apex_locate.py` 可执行且产出合理
- [ ] 三 skill SKILL.md 互相加 "下一步"，meta-skill SKILL.md 完整
- [ ] `.gitignore` 存在且覆盖 `__pycache__` + 验证输出
- [ ] 每 skill evals.json 含 Phase N 基线作为永久回归测试
- [ ] 单脚本平均 ≤ 200 行，整体行数缩减 ≥ 20%

全部勾选后，最终汇总 commit message 或 PR 描述。

---

## Self-Review 笔记

**Spec 覆盖（spec §10 → plan task 映射）**：
- §10.1 Phase 0 → Task 1, 2, 3
- §10.2 重命名 → Task 4
- §10.3 公共库 → Task 5, 6
- §10.4 AGENTS.md 幂等 → Task 7
- §10.5 JSON / events.jsonl → 已嵌在 Task 5（Emitter）+ Task 16（模板）
- §10.6 重构 18 脚本 → Task 9, 10
- §10.7 普适性补丁 → Task 11, 12, 13, 14, 15
- §10.8 idx_diff → Task 17
- §10.9 Kotlin/Rust 告知 → Task 16, 18
- §10.10 Meta-skill → Task 19
- §10.11 SKILL.md 互指 → Task 19
- §10.12 Phase N → Task 20, 21
- §10.13 沉淀 evals → Task 22

**类型一致性**：`Finding` / `Emitter` 在所有 task 中 method 名一致（`emit(Finding(...), confidence=, source=, tags=)`）。`make_parser` 注入的 flag 名一致（`--root` / `--json` / `--no-events` / `--timeout`）。`scan_partitions` 签名一致。

**未明确的小决定**（实施时按下述执行）：
- 单脚本 `Emitter.SCRIPT_VERSION` 全部用 `"1.0.0"`，未来按需 bump
- `idx_diff.py` bucket 聚合用前两路径段，简单可读
- compare_baseline 的 ±10% 阈值可调，先用此值

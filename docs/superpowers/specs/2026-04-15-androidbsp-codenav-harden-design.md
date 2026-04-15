# Android BSP Code-Nav 三 skill 加固 + Meta-skill 引入 设计文档

- 日期：2026-04-15
- 状态：draft（待用户审阅）
- 范围：`androidbsp-codeindex-setup`、`androidbsp-codecross-setup`、`androidbsp-alltrace-setup`（改名 `androidbsp-domaintrace-setup`）、新增 `androidbsp-codenav` meta-skill
- 验证目标：`/home/leo/atk-rk3568_androidR_release_v1.4_20250104/`（Android R / RK3568）
- 兼容基线：Android 11+（SDK 30+），所有主流 vendor 平台

---

## 1. 背景与目标

### 1.1 现状

三个 skill 已基本可用，但存在以下问题：

- **AGENTS.md 写入策略不一致**：codeindex 用 `cp` 覆盖，codecross/alltrace 用标记块追加 → codeindex 重跑会抹掉另两个 skill 的注入段落
- **下游 skill 前置判据弱**：仅 `grep -q "androidbsp-codeindex-setup" AGENTS.md`，用户手改即误判
- **脚本 18 个，重复代码 ~25%**：subprocess 包装、rg 调用、argparse 样板、emit 输出在每个脚本里重复实现
- **脏文件入库**：`scripts/__pycache__/` 内含 codecross 脚本的 .pyc，且仓库无 `.gitignore`
- **输出格式不利于 AI 处理**：纯 TSV，AI 必须正则解析；跨会话无累积
- **普适性靠"现有就够用"**：很多 Android 11+ 引入的特性（GKI、vendor_dlkm、APEX、system_ext、AIDL 多 backend、bootconfig、aconfig 等）现有脚本不识别
- **可发现性弱**：用户必须分别记住三个命令，三 skill 互不指引
- **无回归保障**：冒烟测试只验证 `--help` 能跑，不验证真实查询输出
- **alltrace 命名失语**：实际职责是"领域知识驱动多步追踪"，名字未表达

### 1.2 目标

| # | 目标 | 验证方式 |
|---|---|---|
| G1 | 修复所有上述问题，三 skill 行为对称、互不破坏 | atk 上重复部署 codeindex 后，codecross/domaintrace 注入段落不丢 |
| G2 | 为未来 runtime-trace skill 留接口，但不写 runtime 代码 | events.jsonl schema 文档化、`source` 字段命名空间约定 |
| G3 | Android 11+ 全覆盖、跨 vendor 普适 | atk（RK3568, Android R）跑通；脚本对 Android 11→15 特征做"特征探测而非版本探测" |
| G4 | 重构后输出与重构前一致或更优 | Phase 0 baseline + Phase N 自动 diff 通过 |
| G5 | 公共代码下沉 ~25%，单脚本平均 ≤ 200 行 | `wc -l` 对比 |
| G6 | AI 用户用 `/codenav setup-all` 即可一步完成完整部署 | 命令存在且生效 |

### 1.3 非目标（YAGNI）

- 不接入 rust-analyzer：atk Rust 真实代码 ~10 个文件，性价比极低
- 不接入 Kotlin LSP：gtags 语法级覆盖足够；类型推断盲区在 AGENTS.md 显式告知
- 不实现 jsonl→sqlite ETL：留待未来
- 不实现 events.jsonl 自动轮转：用户手动归档即可
- 不重构 dispatcher（`xlang_find.py` / `domain_find.py`）：本就轻
- 不实现 runtime 数据采集：是未来独立 skill 的事

---

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────────────┐
│                   /codenav setup-all (meta-skill)                  │
│                       androidbsp-codenav                           │
│   (仅编排，不做实际部署；未来扩展 status / clean / doctor)         │
└─────────────────────────────┬──────────────────────────────────────┘
                              │ 顺序调起
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────────┐ ┌──────────────────┐ ┌──────────────────────┐
│ androidbsp-       │ │ androidbsp-      │ │ androidbsp-          │
│ codeindex-setup   │ │ codecross-setup  │ │ domaintrace-setup    │
│ (基石: 索引/工具) │ │ (符号编码跨边界) │ │ (领域知识多步追踪)   │
└───────────────────┘ └──────────────────┘ └──────────────────────┘
        │                     │                     │
        ▼                     ▼                     ▼
              都写入 / 共享 $BSP_ROOT/.codenav/
              ├── scripts/                  (脚本 + 公共库)
              │   ├── _bsp_common.py        (新, 部署自 codeindex)
              │   ├── bsp_filter_gen.py
              │   ├── arg.sh
              │   ├── jni_bridge.py
              │   ├── aidl_bridge.py
              │   ├── syscall_trace.py
              │   ├── ioctl_trace.py
              │   ├── xlang_find.py
              │   ├── dt_bind.py
              │   ├── sysfs_attr.py
              │   ├── binder_svc.py
              │   ├── selinux_trace.py
              │   ├── subsys_trace.py
              │   ├── prop_trace.py
              │   ├── build_trace.py
              │   ├── initrc_trace.py
              │   ├── kconfig_trace.py
              │   ├── firmware_trace.py
              │   ├── netlink_trace.py
              │   ├── media_topo.py
              │   ├── domain_find.py
              │   ├── bootcfg_trace.py        (新)
              │   ├── apex_locate.py          (新)
              │   └── idx_diff.py             (新)
              ├── active_files.idx            (从 $BSP_ROOT/.active_files.idx 迁入)
              ├── active_files.idx.prev       (idx_diff 用)
              ├── gtags.files                 (从根迁入)
              ├── events.jsonl                (新, runtime 接入点)
              └── .version                    (各 skill 版本登记)

              工具链硬性要求保留在 $BSP_ROOT/ 根：
              ├── compile_commands.json       (clangd 搜索 parent dir)
              ├── GTAGS / GRTAGS / GPATH      (gtags 工作目录)
              ├── .clangd                     (clangd 自动查找)
              ├── AGENTS.md                   (项目级 AI 规则)
              └── CLAUDE.md                   (项目级 AI 规则)
```

### 2.1 命名约定

| 旧 | 新 |
|---|---|
| `androidbsp-alltrace-setup` | `androidbsp-domaintrace-setup` |
| `AGENTS.md.alltrace.template` | `AGENTS.md.domaintrace.template` |
| `<!-- BEGIN: androidbsp-alltrace-setup -->` | `<!-- BEGIN: androidbsp-domaintrace-setup -->` |
| `/alltrace --setup` | `/domaintrace setup`（去 `--`） |
| `/code-cross --setup` | `/code-cross setup`（去 `--`） |
| `/codeindex setup`（已对） | 不变 |
| 新增 | `/codenav setup-all` |

### 2.2 子命令风格统一

所有 skill 命令统一为**子命令风格**（无 `--`），符合 git/docker/cargo/kubectl 主流约定：动作是子命令，flag 才用 `--`。

---

## 3. 公共库 `_bsp_common.py`

### 3.1 唯一事实源与部署

- 源码位置：`skills/androidbsp-codeindex-setup/scripts/_bsp_common.py`
- 部署位置：`$BSP_ROOT/.codenav/scripts/_bsp_common.py`
- 部署者：`androidbsp-codeindex-setup` 的 Phase 2
- 其它两个 setup skill **不重复部署**，前置检查保证它已就位
- 升级策略：`packaging.version.Version` 比较，旧版才覆盖

### 3.2 对外暴露的 API（10 项）

```python
"""_bsp_common.py · Shared primitives for androidbsp code-nav scripts."""
from packaging.version import Version

BSP_COMMON_VERSION = Version("1.0.0")

# 工件发现
def find_bsp_root(start: Optional[Path] = None) -> Path: ...
def load_active_files(bsp_root: Path) -> Optional[set[str]]: ...
def parse_compile_commands(bsp_root: Path) -> list[dict]: ...

# 多分区/多路径
def first_existing(candidates: list[Path]) -> Optional[Path]: ...
def scan_partitions(bsp_root: Path, subpath: str) -> list[Path]:
    """对 system / vendor / odm / system_ext / product / apex 6 分区做 subpath 候选探测"""

# subprocess
def run_cmd(cmd: list[str], *, timeout=120, cwd=None, check=False) -> CompletedProcess: ...

# 检索
def rg_find(pattern, *, globs=None, root=None, extra=None, timeout=120
           ) -> list[tuple[str, int, str]]: ...
def gtags_lookup(symbol, *, kind="def", root=None
                ) -> list[tuple[str, int, str]]: ...

# 输出与 events
@dataclass
class Finding:
    tag: str
    file: str
    line: int = 0
    snippet: str = ""
    info: dict = field(default_factory=dict)

class Emitter:
    """统一 --json / --no-events / 写入 .codenav/events.jsonl"""
    def __init__(self, args, script_name: str): ...
    def emit(self, f: Finding, *, confidence="med", source="static-rg",
             tags: list[str] = None) -> None: ...

# 命令行 & 版本
def make_parser(description: str) -> ArgumentParser:
    """预注入 --root / --json / --no-events / --timeout"""
def require_version(min_version: str) -> None: ...
```

### 3.3 共增 1 项分区辅助（对 Android 11+ 多分区现实的应对）

`scan_partitions(bsp_root, subpath)` 对 6 个候选分区做探测，返回所有存在的实际路径：

```python
PARTITIONS = ["system", "vendor", "odm", "system_ext", "product"]
APEX_GLOB = "apex/com.android.*"   # 仅在调用方显式要求时扫描
```

被以下脚本使用：`prop_trace`、`selinux_trace`、`initrc_trace`、`firmware_trace`、`binder_svc`、`build_trace`。

### 3.4 预计代码缩减

- 三 skill 脚本总行数：~5000 → ~3500
- 单脚本平均：280 → 200
- 公共库自身：~280
- **净减 ~1200 行（24%）**

---

## 4. JSON 输出 + events.jsonl 契约

### 4.1 Finding schema（每条结果，含 `--json` 输出）

```jsonc
{
  "schema": "androidbsp.finding/v1",
  "tag": "DECL",
  "file": "kernel/drivers/pci/dwc/pcie-dw-rockchip.c",
  "line": 152,
  "snippet": "static int rockchip_pcie_probe(...)",
  "info": { "compatible": "rockchip,rk3568-pcie",
            "driver": "rockchip_pcie_driver" }
}
```

`--json` 模式下每行一条 Finding（jsonl 格式）。

### 4.2 Event schema（events.jsonl 中每行）

```jsonc
{
  "schema": "androidbsp.event/v1",
  "ts": "2026-04-15T08:21:33Z",
  "source": "static-rg",
  "script": "dt_bind.py",
  "script_version": "1.0.0",
  "query": { "args": ["--compatible", "rockchip,rk3568-pcie"],
             "cwd": "$BSP_ROOT" },
  "finding": { /* Finding 对象 */ },
  "confidence": "med",
  "tags": ["dt", "binding"]
}
```

### 4.3 `source` 命名空间（runtime skill 的契约）

| `source` | 谁写 | 重放性 |
|---|---|---|
| `static-rg` | 本次脚本 | 可重放 |
| `static-gtags` | 本次脚本 | 可重放 |
| `static-clangd` | 未来扩展 | 可重放 |
| `static-error` | 脚本崩溃/超时时自动写 | 可重放 |
| `runtime-ftrace` | 未来 runtime skill | 一次性 |
| `runtime-bpftrace` | 未来 runtime skill | 一次性 |
| `runtime-logcat` | 未来 runtime skill | 一次性 |
| `runtime-dmesg` | 未来 runtime skill | 一次性 |
| `manual` | 用户/AI 人工注入 | 黄金答案 |

### 4.4 写入策略

- **默认开**：所有脚本默认同时输出 stdout 和追加 events.jsonl
- **`--no-events`**：关闭 events.jsonl 写入（手动调试时用）
- **POSIX 原子追加**：`O_APPEND` + 单行 < 4KB → 不需要 flock
- **不去重**：AI 读取时按 `(source, file, line, tag)` 自行 dedup
- **不轮转**：用户超阈值手动 `mv events.jsonl events.archive.<date>.jsonl`

### 4.5 错误事件

脚本崩溃 / 工具不存在 / 超时 → 也写一条 event：
- `source: "static-error"`
- `confidence: "high"`
- `info: {kind, message, traceback}`

让未来 AI 能从历史 error 模式诊断"哪类查询经常失败"。

### 4.6 AGENTS.md 注入新段落

`AGENTS.md.template` 增加：

```markdown
## 历史证据（events.jsonl）

`.codenav/events.jsonl` 是过去所有静态/运行时查询的累积日志。新查询前
应先 `tail -n 200 .codenav/events.jsonl | jq 'select(.tags[] == "dt")'`
看是否已有相关结果。

合并优先级（同 file/line/tag 多条 event）：
- `confidence: high` > `med` > `low`
- `source` 以 `runtime-` 开头优先于 `static-`
- 同源同 confidence，最新 `ts` 优先
```

---

## 5. AGENTS.md 幂等注入

### 5.1 三 skill 行为对称

每 skill 模板首尾自带版本化标记块：

```html
<!-- BEGIN: androidbsp-codeindex-setup v=1 -->
...
<!-- END: androidbsp-codeindex-setup -->
```

注入逻辑（统一抽到 codeindex 的 `_inject_block.sh`，三 skill 共用）：

- 块不存在 → 末尾追加
- 块存在但 `v=` 比模板旧 → 替换 BEGIN…END 之间内容
- 块存在且 `v=` 一致 → 跳过

**用户自定义内容、其它 skill 注入的块永不被破坏**。

### 5.2 codeindex Phase 5 修正

旧：`cp $SKILL_DIR/assets/AGENTS.md.template AGENTS.md`（覆盖）
新：调 `_inject_block.sh` 注入 codeindex 自己的标记块

---

## 6. 普适性策略（Android 11+ 全覆盖）

### 6.1 总原则

**只做特征探测，不做版本探测。** 脚本不问"现在是 Android 几"，只问"这个特征/路径在不在"。

### 6.2 已规划的特征覆盖增量

| 维度 | 影响脚本 | 普适化补丁 |
|---|---|---|
| GKI 内核布局 | `kconfig_trace`、`firmware_trace` | 多候选：`kernel/common/`、`kernel/private/`、`kernel/msm-*`、`kernel/<vendor>-*`、`vendor/<vendor>/kernel/` |
| vendor_dlkm 分区 | `firmware_trace` | 模块路径扩展 `/vendor/lib/modules/`、`/vendor_dlkm/lib/modules/`、`/odm/lib/modules/` |
| `modules.load` / `modules.blocklist` | `firmware_trace` | 解析这些清单 |
| bootconfig（Android 12+） | 新增 `bootcfg_trace.py` | `androidboot.*` 来源探测（cmdline + bootconfig） |
| AIDL 多 backend | `aidl_bridge` | 识别 cpp/ndk/java/rust 各自生成路径 |
| AIDL stable interface 版本 | `aidl_bridge` | 列举 `aidl_api/<iface>/V<n>/` 所有版本 |
| VINTF compat matrix 多文件 | `binder_svc` | `compatibility_matrix.<level>.xml` 多候选 |
| VINTF manifest 多分区 | `binder_svc` | 用 `scan_partitions` 扫 4 分区 manifest |
| system_ext 分区 | `prop_trace`、`selinux_trace`、`build_trace`、`initrc_trace` | `scan_partitions` 增加 system_ext |
| product 分区 | 同上 | `scan_partitions` 增加 product |
| APEX 模块 | 新增 `apex_locate.py` + `build_trace` 增强 | `apex {}` 块识别、`apex_manifest.json` 解析、`/apex/com.android.<name>/` 安装路径 |
| Mainline modules `packages/modules/` | 各脚本路径扫描 | 不再硬编 `frameworks/base/`，扫描 packages/modules |
| SEPolicy mapping `mapping/<sdk>.cil` | `selinux_trace` | 识别兼容层 |
| plat/vendor/odm/system_ext sepolicy | `selinux_trace` | `scan_partitions(.../sepolicy)` |
| CIL 文件 | `selinux_trace` | `.cil` 加入扫描扩展 |
| prefab 模块 | `build_trace` | `prefabs {}` 块识别 |
| aconfig flags（Android 14+） | `build_trace` | `*.aconfig` 文件解析、`Flags.<name>()` 引用 |
| Soong namespaces | `build_trace` | namespace 上下文解析 |
| 多 init.rc 来源 | `initrc_trace` | 6 处来源（system/vendor/odm/system_ext/product/apex/etc/init/） |
| privapp-permissions | （延后）暂不实现 | 留待未来需要时单独评估，本次不做 |

### 6.3 优雅降级模式

特征不存在时**不报错**，输出 `WARN` Finding 后跳过：

```python
sepolicy_dirs = [d for d in scan_partitions(bsp_root, 'etc/selinux') if d.exists()]
if not sepolicy_dirs:
    emitter.emit(Finding(tag='WARN', file='-',
                         info={'msg': 'no sepolicy dir in any partition'}),
                 confidence='low')
    return
```

### 6.4 Kotlin / Rust 已知盲区

`AGENTS.md.template` 新增：

```markdown
## 已知盲区与降级策略

| 语言 | 索引覆盖 | 降级方式 |
|---|---|---|
| C / C++ | clangd 语义 + gtags 符号 | — |
| Java | gtags (universal-ctags) | — |
| Kotlin | 仅语法级（无类型推断） | 跨文件类型问题 → rg 全文 + 手读源码 |
| Rust | 仅文本搜索 | 任何 Rust 查询 → rg 为主 |
| AIDL/HIDL/DT | codecross / domaintrace 覆盖 | 见对应段落 |
```

codeindex Phase 4 冒烟新增 `INFO` 输出告知 Rust/Kotlin 文件量。

---

## 7. Meta-skill `androidbsp-codenav`

### 7.1 文件结构

```
skills/androidbsp-codenav/
├── SKILL.md
└── evals/evals.json
```

### 7.2 SKILL.md 关键段

```yaml
---
name: androidbsp-codenav
description: 'Android BSP 代码导航元 skill：编排 codeindex / codecross /
              domaintrace 三个 setup skill 的完整部署。当用户说「全套部署
              codenav」「一次性配置 BSP 代码导航」「/codenav setup-all」时使用。'
command: /codenav
args:
  - name: setup-all
    description: '依次部署 codeindex → codecross → domaintrace'
  - name: status
    description: '（预留，本次不实现）报告 BSP 当前 codenav 部署状态'
---
```

### 7.3 `setup-all` 行为

依次以"完成且通过冒烟才进下一步"的方式触发：
1. `androidbsp-codeindex-setup` 的 `setup`
2. `androidbsp-codecross-setup` 的 `setup`
3. `androidbsp-domaintrace-setup` 的 `setup`

任一步失败立即停止并输出失败原因。三个 setup skill 仍可独立调用。

### 7.4 `status` 预留

未来报告：deployment 状态、`_bsp_common` 版本、AGENTS.md 三段齐全性、events.jsonl 大小/最近 N 条。本次只在 SKILL.md 列出，不实现。

---

## 8. 工程清理

### 8.1 立即清理

```bash
rm -rf skills/androidbsp-codeindex-setup/scripts/__pycache__
```

### 8.2 新增 `.gitignore`

仓库根新增 / 追加：

```gitignore
__pycache__/
*.pyc
*.pyo
.vscode/
.idea/
*.swp

# 验证基线（机器特定，不入库）
skills/_validation/baseline_atk/
skills/_validation/run_*/
```

### 8.3 三 skill 互相 "下一步" 指引

| skill | 加在 | 指向 |
|---|---|---|
| codeindex | Phase 5 末尾 | "下一步：跑 `/code-cross setup` 和 `/domaintrace setup`，或 `/codenav setup-all` 一步到位" |
| codecross | 部署完末尾 | "姐妹 skill：`/domaintrace setup`" |
| domaintrace | 部署完末尾 | "姐妹 skill：`/code-cross setup`；未来：runtime-trace 通过 `.codenav/events.jsonl` 接入" |

---

## 9. 验证流程（atk-rk3568）

### 9.1 Phase 0：基线采集

**目的**：在动 skill 之前记录"现状到底是什么样"，作为重构后回归对照。

**步骤**：
1. 把**当前**三个 skill 原样部署到 `/home/leo/atk-rk3568_androidR_release_v1.4_20250104/`
2. 跑一次 setup（前置检查 + Phase 1-5 全套）
3. 运行下表 24 条基线查询，每条结果存入 `skills/_validation/baseline_atk/<script>__<query>.txt`
4. 同时记录：执行耗时、退出码、stderr

**基线查询集**：

| # | 脚本 | 查询 | 期望 |
|---|---|---|---|
| 1 | `arg.sh` | `rockchip_pcie_probe` | 至少 1 命中 |
| 2 | `arg.sh` | `definitely_nonexistent_symbol_xyz123` | 空，exit 0 |
| 3 | `gtags_lookup` | `printk` | 大量命中，不卡死 |
| 4 | `jni_bridge` | `JNI_OnLoad` | framework JNI 入口若干 |
| 5 | `aidl_bridge` | `ICameraProvider` | HIDL 实现 |
| 6 | `aidl_bridge` | `IRadio` | AIDL 路径 |
| 7 | `syscall_trace` | `openat` | userspace + kernel 两侧 |
| 8 | `ioctl_trace` | `BINDER_WRITE_READ` | binder driver |
| 9 | `dt_bind` | `rockchip,rk3568-pcie` | DTS + driver 双向 |
| 10 | `sysfs_attr` | `current_temp` | 散热相关 attr |
| 11 | `binder_svc` | `android.hardware.camera.provider` | 注册位置 |
| 12 | `binder_svc` | `ICameraProvider` | 与 #5 对照（不应一模一样） |
| 13 | `selinux_trace` | `untrusted_app` | te + contexts |
| 14 | `subsys_trace` | `clk_pcie_aux` | provider/consumer/DT |
| 15 | `prop_trace` | `ro.product.model` | 多源读写 |
| 16 | `prop_trace` | `ro.vendor.region` | 验证多分区候选 |
| 17 | `build_trace` | `libbinder` | bp + 安装路径 |
| 18 | `initrc_trace` | `vendor.power.stats` | trigger + service |
| 19 | `kconfig_trace` | `CONFIG_DRM_ROCKCHIP` | defconfig + ifdef |
| 20 | `firmware_trace` | `rk_tb_8852be_fw.bin` | request + 打包 |
| 21 | `netlink_trace` | `NL80211` | family 注册 + 用户态 |
| 22 | `media_topo` | `rkisp` | subdev + pad link |
| 23 | `xlang_find` | dispatcher 走通 | 自动派发到 syscall_trace |
| 24 | `domain_find` | dispatcher 走通 | 自动派发到 dt_bind |

5. 跑完后人工抽查 5-10 条，确认输出"看上去合理"——这是 Phase 0 唯一的"对错判断"，结果作为后续比较锚点

**Phase 0 还会顺便暴露**：
- 真零命中的脚本（路径探测 bug、正则太严、新版本 syntax）
- 超时脚本（atk 树规模触发慢路径）
- 输出格式问题（奇怪字符、行号错位）

**这些 bug 加入实施清单**，与原计划的改进合并。

### 9.2 Phase N：重构后对比

完全相同 24 条查询，重新部署后重跑：

| 维度 | 期望 |
|---|---|
| 核心命中行数 | ≈ baseline（±10% 容忍） |
| 之前已知应有但零命中 | 现在命中（修了 path 探测 bug） |
| JSON 输出结构 | 通过 schema 校验 |
| events.jsonl 内容 | 24 条查询全有对应 event，schema 通过 `jsonschema` 库校验 |
| 单脚本平均行数 | ≤ 200（基线 ~280） |
| 整体行数缩减 | ≥ 20% |

### 9.3 自动 diff 工具

`scripts/compare_baseline.py`（位于本仓库 `skills/_validation/`，不部署到 BSP）：

```
$ python3 skills/_validation/compare_baseline.py \
    --before skills/_validation/baseline_atk/ \
    --after  skills/_validation/run_2026-04-N/
=== 24 queries compared ===
✅  19 unchanged (within 10% line count)
⚠️   3 improved  (new hits: dt_bind, prop_trace, kconfig_trace)
❌   2 regressed  (firmware_trace lost 12 hits, syscall_trace empty)
=== regressions ===
  firmware_trace.py rk_tb_8852be_fw.bin
    BEFORE: 14 hits / 0.8s / exit 0
    AFTER:  2 hits  / 0.7s / exit 0
    DIFF:   ...
```

### 9.4 沉淀到 evals

Phase N 通过后，把基线查询集 + 期望命中数（不是完整输出）沉淀到每个 skill 的 `evals/evals.json`，作为永久回归测试。

---

## 10. 实施顺序

按以下顺序推进（详细每步在后续 plan 文档展开）：

1. **Phase 0**：清理 `__pycache__`、加 `.gitignore`、原样部署到 atk、跑 24 条基线、记录 baseline、收集 Phase 0 暴露的 bug 列表
2. **重命名**：`alltrace` → `domaintrace`（目录、文件、模板、命令、SKILL 描述、evals）
3. **公共库** `_bsp_common.py`：写出 ~280 行库 + 部署逻辑
4. **AGENTS.md 幂等**：`_inject_block.sh`、模板加 `v=` 标记、codeindex Phase 5 改用注入
5. **JSON / events.jsonl**：`Emitter` + schema 文档化 + 模板新段落
6. **重构 18 脚本** 切公共库（按依赖顺序：subprocess/argparse → rg/gtags → emit/Finding）
7. **普适性补丁** 8 个脚本扩展 + 2 个新脚本（`bootcfg_trace.py`、`apex_locate.py`）
8. **idx_diff** + `bsp_filter_gen.py` 自动备份 prev
9. **Kotlin/Rust 告知** 模板 + Phase 4 INFO
10. **Meta-skill** `androidbsp-codenav`
11. **三 SKILL.md 互相指引** 加"下一步"段
12. **Phase N**：重新部署 atk、跑同一 24 条、`compare_baseline.py` 对比、修回归
13. **沉淀到 evals**：每 skill 的 evals.json 加新测试

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| 公共库引入 regression | Phase 0 baseline + Phase N 自动 diff |
| 多脚本并发 append events.jsonl 撕裂 | 单行 < 4KB + O_APPEND，POSIX 保证原子 |
| schema 升级冲突 | 顶层 `schema: ".../v1"` 字段，AI 按版本解析 |
| `_bsp_common` 用户手改被覆盖 | `packaging.version` 比较，旧版才覆盖 |
| 重命名遗漏文件引用 | grep `alltrace` 全仓库扫描，零命中才算完成 |
| Android 11+ 特征探测漏 | Phase 0 的 24 条基线 + 用户在生产其它 BSP 上反馈 |
| atk 上某些查询本就零命中（不是 bug） | Phase 0 抽查时人工标注"已知零命中"白名单 |
| `bootcfg_trace.py` / `apex_locate.py` 设计未细化 | 留到 plan 文档具体设计；本 spec 只承诺存在 |

---

## 12. 验收标准

本设计实施完成的标志：

- [ ] 三 skill 重命名完成，全仓库 `grep alltrace` 零命中
- [ ] `_bsp_common.py` 部署到 atk 的 `.codenav/scripts/`，所有脚本 import 成功
- [ ] `/codenav setup-all` 在干净 atk 上可一次性走通
- [ ] 三 skill 对 AGENTS.md 用标记块注入，互不破坏
- [ ] 24 条基线查询 Phase N vs Phase 0 自动 diff 通过：`compare_baseline.py` 报告 0 项 ❌ regressed，且 ≥ 3 项 ⚠️ improved
- [ ] events.jsonl 在 atk 上有数据，每条 event 通过 `jsonschema` 库的 `androidbsp.event/v1` 校验
- [ ] `idx_diff.py`、`bootcfg_trace.py`、`apex_locate.py` 可执行且产出合理
- [ ] 三 skill SKILL.md 互相加"下一步"指引，meta-skill SKILL.md 写完整
- [ ] 仓库根有 `.gitignore`，`__pycache__` 已清
- [ ] 每 skill 的 evals.json 包含本轮基线查询作为永久回归测试
- [ ] 单脚本平均行数 ≤ 200，整体行数缩减 ≥ 20%

---

## 13. 未来工作（不在本设计范围）

- runtime-trace skill：通过 `.codenav/events.jsonl` 写入 `runtime-*` source 事件
- jsonl → sqlite ETL：当 events.jsonl > 100MB 时考虑
- clangd 语义回退：codecross/domaintrace 在 rg 零命中时调 LSP
- `/codenav status` / `/codenav clean` / `/codenav doctor` 的实际实现
- aconfig flags 深度解析（目前只识别）
- VINTF compat matrix 自动诊断

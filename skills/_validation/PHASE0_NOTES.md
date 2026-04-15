# Phase 0 Baseline Notes (atk-rk3568, Android R)

**Date**: 2026-04-15
**BSP**: `/home/leo/atk-rk3568_androidR_release_v1.4_20250104/`
**Lunch target**: `rk3568_r-userdebug`
**Build state**: 已编译，存在 `out/target/product/rk3568_r/`
**Pre-existing artifacts (Mar 25-29)**:
- `GTAGS / GRTAGS / GPATH` (1.2 GB, Mar 25) — 全树扫描，含 kernel C 源
- `compile_commands.json` (1.5 GB, Mar 25) — Soong/userspace only, **no kernel**
- `.active_files.idx` (Mar 29) — **由历史错误调用 `2>&1` 污染**：前 16 行为 stderr 日志，已重新生成

## Findings

### 🐛 Phase 0 BUG-1：active_files.idx 缺少 kernel C 源
**症状**：`grep -c "^kernel/" .active_files.idx` 只有 12 个文件，全是 DTS。kernel 驱动 `.c` 源全缺。
**根因**：`bsp_filter_gen.py` 用 `compile_commands.json` 作为主数据源，而 Android 的 compdb 只覆盖 Soong/userspace，不覆盖 kernel build。
**影响**：
- ❌ `arg.sh <kernel_symbol>` 返回空（query #01）
- ✅ `global -d <kernel_symbol>` 工作正常（GTAGS 是 Mar 25 全树扫描建的）
- ✅ 大部分 domain scripts 走 rg 直扫 BSP，不受影响
**修复方案**：bsp_filter_gen.py 增加 kernel 源扫描（`make CC=clang ... O= LLVM=1 compile_commands.json` 或扫描 Kbuild）。**待办，不在本轮 plan 范围**——这是一个独立的待规划改进。
**短期对策**：AGENTS.md 新增"已知盲区"段告诉 AI："arg.sh 在 kernel C 上可能漏，需要内核符号请用 global"。

### 🐛 Phase 0 BUG-2：bsp_filter_gen.py 接受 `2>&1` 重定向时污染输出
**症状**：用户某次跑 `python3 bsp_filter_gen.py ... > .active_files.idx 2>&1`（或类似形式）后，前 16 行为日志。
**根因**：脚本设计依赖 stderr 不被重定向，但 SKILL.md 的命令示例没明确警示。
**影响**：只要用户错配重定向，所有依赖 idx 的工具（arg.sh、未来 gtags.files filter）都失效。
**修复方案**：bsp_filter_gen.py 加 `--output FILE` 参数（已在 Plan Task 6 设计），写文件而不是 stdout，自然规避此 bug。
**已纳入 Plan**：Task 6 Step 1。

### ⚠️ Phase 0 OBS-1：脚本 CLI 接口与 plan 不一致
**症状**：当前脚本用 `--from-c SYMBOL` / `--compatible PATTERN` / `--service NAME` 等命名 flag，**不接受**位置参数。
**根因**：plan 里设计的 `make_parser()` 把第一个参数当 positional，但当前脚本是命名 flag 风格。
**影响**：runner 必须用对的 flag 才能跑通。已修正 `run_baseline.sh`。
**Plan 调整**：Task 9-10 重构时**保留**当前命名 flag 接口（已是用户用惯的形式），公共库 `make_parser` 不强制 positional symbol。

### 📊 24 条查询结果

| # | 查询 | exit | 行数 | 评价 |
|---|---|---|---|---|
| 01 | arg rockchip_pcie_probe | 123 | 0 | 受 BUG-1 限制（kernel 不在 idx） |
| 02 | arg nonexistent | 123 | 0 | 预期空，但 xargs 返回 123 而非 0 |
| 03 | gtags printk refs head -200 | 1 | 200+ | 截断造成 exit 1，符合预期 |
| 04 | jni JNI_OnLoad | 0 | 93 | ✅ |
| 05 | aidl ICameraProvider (HIDL) | 0 | 6 | ✅ |
| 06 | aidl IRadio | 0 | 17 | ✅ |
| 07 | syscall openat | 0 | 5 | 命中数偏少，可能 kernel 端漏 |
| 08 | ioctl BINDER_WRITE_READ | 0 | 12 | ✅ |
| 09 | dt rockchip,rk3568-pcie | 0 | 9 | ✅ |
| 10 | sysfs current_temp | 0 | 1 | 1 行——可能是 "no hits" 行 |
| 11 | binder svc camera.provider | 0 | 33 | ✅ |
| 12 | binder svc ICameraProvider | 0 | 37 | ✅ |
| 13 | selinux untrusted_app | 0 | 27 | ✅ |
| 14 | subsys clk_pcie_aux | 0 | 1 | 可能找不到此 clock 名 |
| 15 | prop ro.product.model | 0 | 21 | ✅ |
| 16 | prop ro.vendor.region | 0 | 1 | 可能不存在此 prop |
| 17 | build libbinder | 0 | 256 | ✅ |
| 18 | initrc vendor.power.stats | 0 | 1 | 可能不存在此 service |
| 19 | kconfig CONFIG_DRM_ROCKCHIP | 0 | 89 | ✅ |
| 20 | firmware rk_tb_8852be_fw.bin | 0 | 1 | 可能此固件名不对 |
| 21 | netlink NL80211 | 0 | 52 | ✅ |
| 22 | media rkisp | 0 | 75 | ✅ |
| 23 | xlang openat | 0 | 12 | ✅ dispatcher 工作 |
| 24 | domain rockchip,rk3568-pcie | 0 | 9 | ✅ dispatcher 工作 |

## Phase N 预期回归判断

- **改善** 应有：04-09, 11-15, 17, 19, 21-22 — JSON 输出可读化、events.jsonl 累积
- **保持** 应有：23, 24（dispatcher 接口不变）
- **新增改善**：BUG-1 修复后 #01 应有命中
- **可接受不变**：03（head 截断行为）、10/14/16/18/20（具体查询是否有命中取决于 atk 是否真有此符号，与 plan 改造无关）

## 提交追踪

- run_baseline.sh: 已入库（commit 待）
- baseline_atk/*.txt: gitignore，不入库
- 本 NOTES：可考虑入库作为 Phase 0 历史记录

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

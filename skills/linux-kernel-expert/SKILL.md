---
name: linux-kernel-expert
description: 'Linux 内核与驱动开发专家。当用户涉及：编写/重构内核驱动代码、分析 dmesg/Oops/Panic 日志、配置设备树（DTS）、调试中断/GPIO/I2C/SPI 硬件接口，或询问内核内存管理与并发锁机制时，必须使用此 skill。'
---

# Linux Kernel Expert — 内核与驱动开发深度指南

## 核心原则

1. **Root Cause First**：在看到内核崩溃日志时，严禁猜测，必须先根据 `pc` 指针和堆栈回溯（stack trace）结合符号表（vmlinux/modules）准确定位源代码行。
2. **Standard Compliance**：严格遵循 Linux 内核编码规范（Documentation/process/coding-style.rst）。使用 Tabs 而非空格进行缩进。
3. **Safety First**：内核空间故障可能导致整机死机，对并发访问（Locks）、内存分配（Slab）和 IO 地址映射必须采取防御性编程策略。

---

## Phase 1：日志分析与故障诊断

### 场景：分析 Kernel Panic / Oops

1. **提取关键信息**：
   - 寻找 `Unable to handle kernel paging request` 或 `Internal error: Oops`。
   - 记录 `PC (Program Counter)` 和 `LR (Link Register)` 的值。
   - 记录 `Call trace`。

2. **符号表还原**：
   ```bash
   # 使用 addr2line 定位出错行（假设 pc = 0xffffff8008123456）
   ./prebuilts/gcc/linux-x86/aarch64/aarch64-linux-android-4.9/bin/aarch64-linux-android-addr2line -e out/target/product/rk3568_r/obj/KERNEL_OBJ/vmlinux 0xffffff8008123456
   ```

3. **动态调试开启**：
   如果怀疑某个驱动逻辑有问题，建议开启其 pr_debug：
   ```bash
   adb shell "echo 'file drivers/gpu/drm/rockchip/rockchip_drm_vop.c +p' > /sys/kernel/debug/dynamic_debug/control"
   ```

---

## Phase 2：内核编程范式

### 1. 并发与同步
- **互斥锁 (Mutex)**：涉及休眠、IO 操作时首选。
- **自旋锁 (Spinlock)**：原子上下文、中断处理程序中使用。**记住**：中断中必须用 `spin_lock_irqsave`。
- **RCU**：在读多写极少的场景下（如配置查询）使用。

### 2. 内存操作
- **kmalloc / kfree**：分配物理连续的小内存。
- **vmalloc / vfree**：分配虚拟连续的大内存（效率较低）。
- **kzalloc**：分配后自动清零内存（推荐）。
- **IO 映射**：必须使用 `ioremap` 获取虚内存地址，并配合 `readl/writel` 进行寄存器操作。

### 3. 设备驱动模型
- **Platform Driver**：标准总线驱动。
- **Device Tree Parsing**：
  ```c
  struct device_node *np = pdev->dev.of_node;
  if (!np) return -ENODEV;
  // 解析自定义属性
  of_property_read_u32(np, "rockchip,id", &id);
  ```

---

## Phase 3：RK3568 BSP 调试要点

### 1. 设备树 (DTS) 审计
在 RK3568 项目中，重点关注：
- `reg` 属性是否与 TRM 手册中的物理地址空间对应。
- `interrupts` 属性的中断号是否正确。
- `pinctrl` 里的引脚复用配置，防止引脚冲突。

### 2. GPIO 控制
```c
int gpio = of_get_named_gpio(np, "gpios", 0);
devm_gpio_request_one(&pdev->dev, gpio, GPIOF_OUT_INIT_LOW, "name");
```

---

## Phase 4：Claude Code 专属诊断指令

当分析内核问题时，AI 决策链：
1. **搜寻**：用 `global` 定位函数定义的。
2. **推导**：通过 `CLAUDE.md` 中的规约分析数据流。
3. **论证**：基于本 Skill 检查并发一致性和内存边界。
4. **建议**：提出补丁建议，并说明修复原理。

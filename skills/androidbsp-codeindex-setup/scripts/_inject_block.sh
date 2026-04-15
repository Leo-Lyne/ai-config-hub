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

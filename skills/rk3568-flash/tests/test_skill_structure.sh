#!/usr/bin/env bash
# Repeatable structural sanity check for the skill (no device required).
# Run: bash tests/test_skill_structure.sh
set -e
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0
chk() { local desc="$1"; shift; if "$@"; then echo "  ✓ $desc"; else echo "  ✗ $desc"; fail=$((fail+1)); fi; }

echo "[1] file presence"
chk "SKILL.md"                  test -f "$SKILL/SKILL.md"
chk "lib/config.sh"             test -f "$SKILL/lib/config.sh"
chk "scripts/flash.sh"          test -x "$SKILL/scripts/flash.sh"
chk "scripts/transports/_common.sh"   test -f "$SKILL/scripts/transports/_common.sh"
chk "scripts/transports/windows.sh"   test -x "$SKILL/scripts/transports/windows.sh"
chk "scripts/transports/vbox_linux.sh" test -x "$SKILL/scripts/transports/vbox_linux.sh"
chk "deps/setup_vbox.sh"        test -x "$SKILL/deps/setup_vbox.sh"
chk "deps/fetch_deps.sh"        test -x "$SKILL/deps/fetch_deps.sh"
chk "deps/linux_upgrade_tool/"  test -x "$SKILL/deps/linux_upgrade_tool/upgrade_tool"
chk "deps/cloud_init/"          test -d "$SKILL/deps/cloud_init"

echo
echo "[2] YAML frontmatter"
python3 - "$SKILL/SKILL.md" <<'PY' || fail=$?
import sys, re, yaml
src = open(sys.argv[1]).read()
m = re.match(r'---\n(.*?)\n---', src, re.S)
assert m, "no frontmatter"
fm = yaml.safe_load(m.group(1))
assert fm.get("name") == "rk3568-flash"
assert len(fm.get("description","")) > 200, "description too short"
print(f"  ✓ name={fm['name']} description={len(fm['description'])}c")
PY

echo
echo "[3] no hardcoded user/project paths"
hits=$(grep -rnE '/home/[a-z]+/atk-rk3568|RK3568_R_USERDEBUG_RK3568-ATK|Image-rk3568_r' \
       "$SKILL/lib" "$SKILL/scripts" "$SKILL/deps" 2>/dev/null \
       | grep -v -E 'Image-\*|RK3568_R_USERDEBUG_\*|win_upgrade_tool_v\*|Linux_Upgrade_Tool_v\*' || true)
if [ -z "$hits" ]; then echo "  ✓ none"; else echo "  ✗ leftover hardcodes:"; echo "$hits"; fail=$((fail+1)); fi

echo
echo "[4] dispatcher works from arbitrary cwd"
( cd /tmp && bash "$SKILL/scripts/flash.sh" status 2>&1 | head -1 | grep -q '^\[rk3568-flash\]' ) && echo "  ✓ status from /tmp" || { echo "  ✗ status from /tmp"; fail=$((fail+1)); }
( cd "$SKILL" && bash "$SKILL/scripts/flash.sh" vbox-linux status 2>&1 | head -1 | grep -q '^\[rk3568-flash\]' ) && echo "  ✓ vbox-linux status" || { echo "  ✗ vbox-linux status"; fail=$((fail+1)); }

echo
if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "$fail FAILURE(S)"; exit 1; fi

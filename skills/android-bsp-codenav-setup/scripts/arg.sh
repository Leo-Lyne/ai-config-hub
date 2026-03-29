#!/bin/bash
# arg: Active Ripgrep — searches only BSP active files
# Usage: arg <rg-options-and-pattern>
#
# Automatically locates .active_files.idx by walking up from $PWD,
# so it works from any subdirectory of the BSP tree.
# Platform-agnostic: works on any Android BSP project.

find_bsp_root() {
    local dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -f "$dir/.active_files.idx" ]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    return 1
}

BSP_ROOT=$(find_bsp_root)
if [ -z "$BSP_ROOT" ]; then
    echo "Warning: .active_files.idx not found in any parent directory. Falling back to full rg." >&2
    exec rg "$@"
fi

ACTIVE_IDX="$BSP_ROOT/.active_files.idx"

# Search from BSP root so relative paths in idx resolve correctly.
# NUL-delimit for safety with special characters in paths.
cd "$BSP_ROOT" || exit 1
tr '\n' '\0' < "$ACTIVE_IDX" | xargs -0 rg -n "$@" 2>/dev/null

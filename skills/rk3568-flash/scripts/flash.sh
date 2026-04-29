#!/usr/bin/env bash
# Main dispatcher for /rk3568-flash skill.
#
# Usage:
#   flash.sh [<transport>] <subcommand> [args...]
#
# Transport (optional, default = windows for backwards compat):
#   windows      Run upgrade_tool.exe natively on Windows via WSL interop.
#                Works as long as Windows is the WSL host and Rockchip USB driver
#                is installed.
#   vbox-linux   Run Linux upgrade_tool inside a headless VBox VM that owns the
#                USB device via VBox USB filter. Use when you want to flash from
#                a clean Linux env (e.g. you suspect Windows USB driver issues),
#                or as a portable alternative.
#
# Subcommand:
#   full [<dir>]                 Whole-image factory flash (UL+DI sequence).
#   parts <p1> [p2]...           Single/multi-partition flash from rockdev/.
#   auto                         mtime-diff: only re-flash partitions changed.
#   setup                        First-time provisioning (vbox-linux only).
#   status                       Print resolved config + tool/device state.

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

# Detect optional leading transport argument.
case "${1:-}" in
    windows|vbox-linux)
        TRANSPORT="$1"; shift
        ;;
    "")
        die "no subcommand. Try: status | full | parts <p>... | auto | setup"
        ;;
    *)
        TRANSPORT="windows"  # default for backwards compat
        ;;
esac

SUBCMD="${1:-}"; shift || true
[ -n "$SUBCMD" ] || die "no subcommand. Try: status | full | parts <p>... | auto | setup"

# Source the chosen transport (defines transport_full/parts/auto/setup/status fns)
TRANSPORT_FILE="$(dirname "${BASH_SOURCE[0]}")/transports/${TRANSPORT//-/_}.sh"
[ -f "$TRANSPORT_FILE" ] || die "no transport implementation: $TRANSPORT_FILE"
. "$TRANSPORT_FILE"

case "$SUBCMD" in
    full)    transport_full "$@" ;;
    parts)   transport_parts "$@" ;;
    auto)    transport_auto "$@" ;;
    setup)   transport_setup "$@" ;;
    status)  transport_status "$@" ;;
    *)       die "unknown subcommand: $SUBCMD (full | parts | auto | setup | status)" ;;
esac

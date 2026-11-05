#!/bin/sh
# Install the root-owned controller. Run through sudo as the desktop user.
set -eu
[ "$(id -u)" -eq 0 ] || { echo 'Run: sudo ./install-ipmi.sh' >&2; exit 1; }
project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
control_uid=${SUDO_UID:-1000}
board=$(cat /sys/class/dmi/id/board_name)
case "$board" in
    *MZ72-HB0*) ;;
    *) echo "Unsupported board: $board. This controller targets MZ72-HB0 only." >&2; exit 1 ;;
esac
command -v ipmitool >/dev/null
command -v sensors >/dev/null
command -v nvidia-smi >/dev/null
ipmitool -I open mc info
ipmitool -I open sdr type fan
install -d -m 755 /usr/local/lib/gigabyte-fan-control
if systemctl is-active --quiet gigabyte-ipmi-fan-control.service; then
    systemctl stop gigabyte-ipmi-fan-control.service
fi
install -m 644 "$project_dir/core.py" "$project_dir/ipmi_daemon.py" /usr/local/lib/gigabyte-fan-control/
install -m 644 "$project_dir/gigabyte-ipmi-fan-control.service" /etc/systemd/system/
install -d -m 755 /etc/systemd/system/gigabyte-ipmi-fan-control.service.d
printf '[Service]\nEnvironment=FAN_CONTROL_UID=%s\n' "$control_uid" > /etc/systemd/system/gigabyte-ipmi-fan-control.service.d/user.conf
systemctl daemon-reload
systemctl enable --now gigabyte-ipmi-fan-control.service

#!/bin/sh
# Run as the desktop user after installing the controller and Python dependencies.
set -eu
[ "$(id -u)" -ne 0 ] || { echo 'Run this script without sudo.' >&2; exit 1; }
project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
[ -x "$project_dir/.venv/bin/python" ] || { echo 'Create .venv and install requirements.txt first.' >&2; exit 1; }
# systemd ExecStart and Desktop Entry Exec have different escaping rules.
"$project_dir/.venv/bin/python" - "$project_dir" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
home=Path.home()
icons=home/'.local/share/icons/hicolor/scalable/apps'
apps=home/'.local/share/applications'
units=home/'.config/systemd/user'
for path in (icons, apps, units): path.mkdir(parents=True,exist_ok=True)
(icons/'gigabyte-fan-control.svg').write_bytes((root/'fan-control.svg').read_bytes())
launcher=str(root/'run.sh')
if any(c in launcher for c in ('"', '\\', '$', '%', '`', '\n', '\r')):
    raise SystemExit('Install from a path without quotes, backslashes, $, %, backticks or line breaks.')
systemd_arg='"'+launcher+'"'
desktop_arg=systemd_arg
(units/'gigabyte-fan-control.service').write_text(f'''[Unit]
Description=IPMI fan control tray
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart={systemd_arg} --tray
Restart=no

[Install]
WantedBy=graphical-session.target
''')
(apps/'gigabyte-fan-control.desktop').write_text(f'''[Desktop Entry]
Type=Application
Name=风扇控制
Name[en]=IPMI Fan Control
Comment=通过本机 IPMI 控制全部风扇，按 CPU/GPU 最高温度调速
Exec={desktop_arg}
Icon=gigabyte-fan-control
Terminal=false
Categories=System;Monitor;
StartupWMClass=gigabyte-fan-control
Keywords=fan;ipmi;风扇;调速;温度;
''')
PY
systemctl --user daemon-reload
systemctl --user enable --now gigabyte-fan-control.service
if command -v kbuildsycoca6 >/dev/null; then kbuildsycoca6 --noincremental; fi

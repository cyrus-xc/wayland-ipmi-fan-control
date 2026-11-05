#!/bin/sh
set -eu
project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"
exec "$project_dir/.venv/bin/python" "$project_dir/app.py" "$@"

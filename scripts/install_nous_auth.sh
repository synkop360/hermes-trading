#!/bin/sh
# Official editable installation, pinned and without agent dependencies.
set -eu
target=$1
python=$2
revision=eb52760564dbba2e5971fa54bd67384e281cd3b8
if [ -e "$target" ]; then
    echo 'Nous auth source destination already exists' >&2
    exit 1
fi
git init "$target"
git -C "$target" remote add origin https://github.com/NousResearch/hermes-agent
git -C "$target" fetch --depth 1 origin "$revision"
git -C "$target" checkout --detach FETCH_HEAD
uv pip install --python "$python" --no-deps --editable "$target"
"$python" -c 'import hermes_cli.auth, hermes_cli.nous_billing'

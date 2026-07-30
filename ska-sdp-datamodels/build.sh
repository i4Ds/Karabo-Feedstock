#!/bin/sh
set -eux

# Only cd if the folder actually exists (conda-build often runs from the source root already)
if [ -n "${INTERNAL_FOLDER_NAME:-}" ] && [ -d "$INTERNAL_FOLDER_NAME" ]; then
  cd "$INTERNAL_FOLDER_NAME"
fi

$PYTHON -m pip install --no-deps . -vv

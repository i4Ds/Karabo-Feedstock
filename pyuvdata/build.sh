#!/bin/sh
set -eux

$PYTHON -m pip install . --no-deps --ignore-installed --no-cache-dir -vv

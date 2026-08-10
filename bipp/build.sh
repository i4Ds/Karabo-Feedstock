#!/bin/bash
set -euxo pipefail

export CMAKE_ARGS="${CMAKE_ARGS:-} -DBLA_VENDOR=OpenBLAS"

BIPP_GPU=OFF $PYTHON -m pip install --no-deps --no-build-isolation .
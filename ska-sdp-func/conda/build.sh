#!/bin/sh

export SKA_SDP_FUNC_LIB_DIR=$CONDA_PREFIX/lib
export CMAKE_ARGS="-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX" 
$PYTHON -m pip install . --no-deps

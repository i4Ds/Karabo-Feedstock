#!/bin/sh

export CMAKE_ARGS="-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX -DCUDA_ARCH=8.0" 
export SKA_SDP_FUNC_LIB_DIR=$CONDA_PREFIX/lib

# install
$PYTHON -m pip install . --no-deps

#!/bin/sh

export SKA_SDP_FUNC_LIB_DIR=$CONDA_PREFIX/lib

mkdir build
cd build
cmake -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX -DCUDA_ARCH=8.0 ..
make
make install
cd ..

export CMAKE_ARGS="-DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX -DCUDA_ARCH=8.0" 
$PYTHON -m pip install . --no-deps

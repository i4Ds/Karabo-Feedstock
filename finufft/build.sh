#!/bin/sh

# create lib for python (specific version)
$PYTHON -m pip install $FILE_NAME 

# create native C++ Lib
wget https://github.com/flatironinstitute/finufft/archive/refs/tags/v$FINUFFT_VERSION_ALT.tar.gz
tar -xzf v$FINUFFT_VERSION_ALT.tar.gz
cd finufft-$FINUFFT_VERSION_ALT

echo -e "CXXFLAGS += -I$CONDA_PREFIX/include -LCONDA_PREFIX/lib\nexport LDFLAGS=-L$CONDA_PREFIX/lib\n" >> make.inc

# create lib
make lib
cp lib/*.so $CONDA_PREFIX/lib
cp lib-static/*.a $CONDA_PREFIX/lib
cp -r ./include $CONDA_PREFIX

# TODO add CUDA target - only in main, not in latest release

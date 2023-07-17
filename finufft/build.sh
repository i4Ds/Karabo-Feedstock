#!/bin/sh

# create lib for python (specific version)
$PYTHON -m pip install $FILE_NAME 

# create native C++ Lib
wget https://github.com/flatironinstitute/finufft/archive/refs/tags/v$FINUFFT_VERSION_ENV.tar.gz
tar -xzf v$FINUFFT_VERSION_ENV.tar.gz
cd finufft-$FINUFFT_VERSION_ENV

echo -e "CXXFLAGS += -I$CONDA_PREFIX/include -L$CONDA_PREFIX/lib\nexport LDFLAGS=-L$CONDA_PREFIX/lib\n" >> make.inc

# create lib
make lib

# cpy to output dir
cp lib/*.so $PREFIX/lib
cp lib-static/*.a $PREFIX/lib
cp -r ./include $PREFIX

# TODO add CUDA target - only in main, not in latest release

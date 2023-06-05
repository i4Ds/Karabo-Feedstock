#!/bin/sh

COMPILER_PATH="$BUILD_PREFIX/bin"

./bootstrap.sh
./configure --prefix=$PREFIX --with-gcc-arch=skylake \
  CC="$COMPILER_PATH/mpicc" F77="$COMPILER_PATH/mpif90" MPICC="$COMPILER_PATH/mpicc" CFLAGS="-O3" FFLAGS="-O3"

make
make install

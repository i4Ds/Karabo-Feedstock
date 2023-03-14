#!/bin/sh

COMPILER_PATH="$BUILD_PREFIX/bin"

./configure --prefix=$PREFIX --enable-mpi --enable-openmp --enable-threads \
							 --enable-avx --enable-avx2 --enable-shared \
							 --enable-float \
  CC="$COMPILER_PATH/mpicc" F77="$COMPILER_PATH/mpif90" MPICC="$COMPILER_PATH/mpicc" CFLAGS="-O3" FFLAGS="-O3"

make
make install

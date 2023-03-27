#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    FFTW3_VERSION=$(echo "${FFTW3_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${FFTW3_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${FFTW3_VERSION}/" meta.yaml
fi

COMPILER_PATH="$BUILD_PREFIX/bin"

# double as base for the lib
./configure --prefix=$PREFIX --enable-mpi --enable-openmp --enable-threads \
							 --enable-avx --enable-avx2 --enable-shared \
  CC="$COMPILER_PATH/mpicc" F77="$COMPILER_PATH/mpif90" MPICC="$COMPILER_PATH/mpicc" CFLAGS="-O3" FFLAGS="-O3"

make
make install

# float instead of double
./configure --prefix=$PREFIX --enable-mpi --enable-openmp --enable-threads \
							 --enable-avx --enable-avx2 --enable-shared \
							 --enable-float \
  CC="$COMPILER_PATH/mpicc" F77="$COMPILER_PATH/mpif90" MPICC="$COMPILER_PATH/mpicc" CFLAGS="-O3" FFLAGS="-O3"

make
make install

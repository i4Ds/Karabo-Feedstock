#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    PFFT_VERSION=$(echo "${PFFT_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${PFFT_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${PFFT_VERSION}/" meta.yaml
fi

COMPILER_PATH="$BUILD_PREFIX/bin"

./bootstrap.sh
./configure --prefix=$PREFIX CC="$COMPILER_PATH/mpicc" F77="$COMPILER_PATH/mpif90" MPICC="$COMPILER_PATH/mpicc"

make
make install

#!/bin/sh
set -eux

echo "CC=${CC}"
echo "CXX=${CXX}"
"${CC}" --version
"${CXX}" --version

cmake \
    -DFIND_CUDA=ON \
    -DCUDAToolkit_ROOT="${CUDA_PATH:-/usr/local/cuda}" \
    -DCMAKE_PREFIX_PATH="${PREFIX}" \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DCMAKE_C_COMPILER="${CC}" \
    -DCMAKE_CXX_COMPILER="${CXX}" \
    -DCUDA_ARCH="6.0;6.1;6.2;7.0;7.5;8.0;8.6;8.7" \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_CXX_STANDARD_REQUIRED=ON \
    .

make -j2 install
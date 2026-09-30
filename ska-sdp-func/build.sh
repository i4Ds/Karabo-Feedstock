#!/bin/bash
set -euxo pipefail

export SKA_SDP_FUNC_LIB_DIR=$CONDA_PREFIX/lib

# CMakeLists.txt uses the legacy find_package(CUDA), whose search does not match conda-forge's
# CUDA layout (nvcc in the build prefix, headers in $PREFIX/targets/<arch>-linux/include,
# libraries in $PREFIX/lib). Give it the locations directly, link the CUDA runtime dynamically
# (cudart/cufft become run dependencies via run_exports), and keep conda's own CMAKE_ARGS.
NVCC=$(command -v nvcc)
CUDA_INC=$(dirname "$(find "$PREFIX/targets" -path '*/include/cuda_runtime.h' | head -n1)")
test -f "$CUDA_INC/cuda_runtime.h"

# GPU architectures: conda-forge's cuda-nvcc exports CUDAARCHS on activation (5.0 to 12.1 incl.
# Hopper and Blackwell, plus PTX for forward compatibility). ska-sdp-func 1.0.0's own
# CUDA_ARCH=ALL stops at 8.6 and rejects newer values, so build -gencode flags from CUDAARCHS
# and pass them through CUDA_NVCC_FLAGS. The kernels use double-precision atomicAdd, which needs
# compute capability 6.0+, so entries below MIN_SM are skipped (upstream's minimum is 6.0 too).
test -n "${CUDAARCHS:-}"
MIN_SM=60
GENCODE=""; WANT_SASS=""
for a in ${CUDAARCHS//;/ }; do
  arch=${a%-*}; n=${arch%[af]}
  [ "$n" -ge "$MIN_SM" ] || continue
  case $a in
    *-real)    GENCODE="$GENCODE;-gencode;arch=compute_${arch},code=sm_${arch}"; WANT_SASS="$WANT_SASS $n" ;;
    *-virtual) GENCODE="$GENCODE;-gencode;arch=compute_${arch},code=compute_${arch}" ;;
  esac
done
GENCODE=${GENCODE#;}

export CMAKE_ARGS="${CMAKE_ARGS:-} -DCMAKE_INSTALL_PREFIX=$CONDA_PREFIX \
  -DCUDA_TOOLKIT_ROOT_DIR=$PREFIX -DCUDA_NVCC_EXECUTABLE=$NVCC \
  -DCUDA_TOOLKIT_INCLUDE=$CUDA_INC -DCUDA_CUDART_LIBRARY=$PREFIX/lib/libcudart.so \
  -DCUDA_cufft_LIBRARY=$PREFIX/lib/libcufft.so -DCUDA_USE_STATIC_CUDA_RUNTIME=OFF \
  -DCUDA_NVCC_FLAGS=$GENCODE"

$PYTHON -m pip install --no-deps . -v

# find_package(CUDA) is optional upstream: fail here rather than ship a CPU-only package.
# Fail unless the library links the CUDA runtime and carries machine code for every real
# architecture that was requested above. cuobjdump names family-specific entries without
# their suffix (100f -> sm_100) and architecture-specific ones with it (90a -> sm_90a), so
# match the number with an optional a/f.
LIB=$(find "$SP_DIR" "$PREFIX/lib" -name 'libska_sdp_func.so' | head -n1)
"${READELF:-readelf}" -d "$LIB" | grep libcudart
SASS=$(cuobjdump --list-elf "$LIB")
for n in $WANT_SASS; do
  grep -qE "sm_${n}[af]?\b" <<<"$SASS" || { echo "ERROR: no sm_${n} code in $LIB"; exit 1; }
done

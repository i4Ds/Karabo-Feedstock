#!/bin/sh
set -eux

echo "CC=${CC}"
echo "CXX=${CXX}"
"${CC}" --version
"${CXX}" --version

# GPU architectures: conda-forge's cuda-nvcc exports CUDAARCHS on activation (5.0 to 12.1,
# incl. Hopper 90a and Blackwell, plus PTX for forward compatibility), and CMake takes it over
# OSKAR's own CUDA_ARCH option, so that list is what gets built. (OSKAR still prints its default
# list during configure; the check at the end looks at what is actually in the library.)
cmake \
    -DFIND_CUDA=ON \
    -DCMAKE_PREFIX_PATH="${PREFIX}" \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DCMAKE_C_COMPILER="${CC}" \
    -DCMAKE_CXX_COMPILER="${CXX}" \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_CXX_STANDARD_REQUIRED=ON \
    .

make -j"${CPU_COUNT:-2}" install

# OSKAR's find_package(CUDAToolkit) is optional: without it the build silently becomes CPU-only.
# Fail unless the library links the CUDA runtime and carries machine code for every real
# architecture in CUDAARCHS, so the check follows conda-forge's list rather than a fixed name.
# cuobjdump names family-specific entries without their suffix (100f -> sm_100) and
# architecture-specific ones with it (90a -> sm_90a), so match the number with an optional a/f.
"${READELF:-readelf}" -d "${PREFIX}/lib/liboskar.so" | grep libcudart
SASS=$(cuobjdump --list-elf "${PREFIX}/lib/liboskar.so")
for a in $(echo "${CUDAARCHS}" | tr ';' ' '); do
    case "$a" in
        *-real) n=${a%-real}; n=${n%[af]}
                echo "$SASS" | grep -qE "sm_${n}[af]?\b" || { echo "ERROR: no sm_${n} code in liboskar.so"; exit 1; } ;;
    esac
done

#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    OSKAR_VERSION=$(echo "${OSKAR_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${OSKAR_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${OSKAR_VERSION}/" meta.yaml
fi


cmake -DCFIND_CUDA=ON -DCMAKE_INSTALL_PREFIX=$PREFIX -DCUDA_ARCH="6.0;6.1;6.2;7.0;7.5;8.0;8.6;8.7"
make -j2 install

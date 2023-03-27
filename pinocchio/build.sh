#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    PINOCCHIO_VERSION=$(echo "${PINOCCHIO_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${PINOCCHIO_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${PINOCCHIO_VERSION}/" meta.yaml
fi

cd ./src
make all
make install

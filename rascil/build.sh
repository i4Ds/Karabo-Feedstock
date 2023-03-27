#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    RASCIL_VERSION=$(echo "${RASCIL_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${RASCIL_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${RASCIL_VERSION}/" meta.yaml
fi

$PYTHON -m pip install . --no-deps

#!/bin/sh

if [ "$DEV_BUILD" = "true" ]; then
    # Replace the patch number with "dev" followed by the original patch number
    TOOLS_21CM_VERSION=$(echo "${TOOLS_21CM_VERSION}" | sed 's/\([0-9]*\.[0-9]*\.\)\([0-9]*\)/\1dev\2/g')
    # Update the version field in the meta.yaml file
    sed -i "s/version: .*/version: ${TOOLS_21CM_VERSION}/" meta.yaml
    sed -i "s/string: .*/string: ${TOOLS_21CM_VERSION}/" meta.yaml
fi

$PYTHON -m pip install --no-deps .

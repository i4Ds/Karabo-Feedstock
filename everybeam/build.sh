#!/bin/bash

# Navigate to the source directory
cd $SRC_DIR

# Ensure the repository is a git repository
if [ -d ".git" ]; then
    echo "Repository detected, checking out the correct commit..."
    git fetch --all
    git checkout 49b61d96785f700cbc200b220478a66b4fedf2d0
else
    echo "Error: Not a git repository. Check the source."
    exit 1
fi

# Proceed with the normal build process
mkdir build && cd build
cmake -DCMAKE_INSTALL_PREFIX=$PREFIX ..
make install

#!/bin/bash

cd $(dirname $(readlink -f $0)); CURRENT=$(pwd); cd -
MACH=$(echo $(basename ${0%.*}) | awk -F - '{print $2}')

rm -rf build; mkdir build && cd build \
    && cmake $CURRENT -DCMAKE_TOOLCHAIN_FILE=$toolchain -DMACHINE=$MACH -Wno-dev && make -j8

#!/usr/bin/env bash
# Cursor Cloud Agent install (update) script. Idempotent; runs from repo root after git pull.
set -euo pipefail

export CC=gcc-13
export CXX=g++-13
export PATH="/usr/local/bin:${PATH:-/usr/bin:/bin}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

bash .cursor/setup-conan-profile.sh

conan install . --build=missing -s build_type=Release

cmake -S . -B build/Release -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/build/Release/generators/conan_toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=gcc-13 \
  -DCMAKE_CXX_COMPILER=g++-13

cmake --build build/Release

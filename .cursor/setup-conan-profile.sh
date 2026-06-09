#!/usr/bin/env bash
# Idempotent: ensure the gcc-13 Conan profile matches CI (.github/workflows/ci.yml).
set -euo pipefail

mkdir -p "${CONAN_HOME:-$HOME/.conan2}/profiles"

cat > "${CONAN_HOME:-$HOME/.conan2}/profiles/default" <<'EOF'
[settings]
arch=x86_64
build_type=Release
compiler=gcc
compiler.cppstd=17
compiler.libcxx=libstdc++11
compiler.version=13
os=Linux

[conf]
tools.build:compiler_executables={"c": "gcc-13", "cpp": "g++-13"}

[buildenv]
CC=gcc-13
CXX=g++-13
EOF

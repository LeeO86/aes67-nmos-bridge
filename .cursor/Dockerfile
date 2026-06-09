# Cursor Cloud Agent base image for aes67-nmos-bridge (C++/CMake + Conan 2 + nmos-cpp).
# Workspace is mounted at runtime; only dependency metadata is copied here to warm caches.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc-13 \
    g++-13 \
    cmake \
    ninja-build \
    libavahi-compat-libdnssd-dev \
    avahi-daemon \
    python3 \
    python3-pip \
    sudo \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages "conan~=2.20"

COPY .cursor/setup-conan-profile.sh /usr/local/bin/setup-conan-profile.sh
RUN chmod +x /usr/local/bin/setup-conan-profile.sh && setup-conan-profile.sh

# Pre-warm the Conan package cache using the repo's conanfile (build context = repo root).
WORKDIR /opt/conan-prewarm
COPY conanfile.txt /opt/conan-prewarm/conanfile.txt
RUN conan install . --build=missing -s build_type=Release

WORKDIR /workspace

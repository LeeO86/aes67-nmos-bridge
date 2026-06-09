# Cursor Cloud Agent base image for aes67-nmos-bridge (C++/CMake + Conan 2 + nmos-cpp).
#
# Keep this image build fast and reliable: system deps + Conan + profile only.
# Heavy `conan install --build=missing` runs in environment.json `install` (after
# the workspace is mounted) so a failed/slow dependency build cannot brick image creation.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    CC=gcc-13 \
    CXX=g++-13 \
    PATH=/usr/local/bin:/usr/bin:/bin

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc-13 \
    g++-13 \
    build-essential \
    clang-18 \
    make \
    cmake \
    ninja-build \
    libavahi-compat-libdnssd-dev \
    libavahi-client-dev \
    avahi-daemon \
    dbus \
    libboost-all-dev \
    libasound2-dev \
    libfaac-dev \
    alsa-utils \
    python3 \
    python3-pip \
    python3-venv \
    sudo \
    git \
    ca-certificates \
    bash \
    curl \
    && ln -sf /usr/bin/clang-18 /usr/bin/clang \
    && ln -sf /usr/bin/clang++-18 /usr/bin/clang++ \
    && rm -rf /var/lib/apt/lists/*

# Conan CLI for all users (system-wide install on Ubuntu 24.04).
RUN pip3 install --break-system-packages "conan~=2.20" \
    && conan --version

COPY .cursor/setup-conan-profile.sh /usr/local/bin/setup-conan-profile.sh
RUN chmod +x /usr/local/bin/setup-conan-profile.sh \
    && setup-conan-profile.sh

WORKDIR /workspace

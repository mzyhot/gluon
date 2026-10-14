#!/bin/bash
# Check source declarations and native feature negotiation against pinned vk.xml.
# This is a source-text check: it does not compile or load graphics/Wine code.
# Usage: check-vulkan-portability.sh <patched-wine-source> [download-cache]
set -euo pipefail
GLUON_VK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GLUON_VK_SOURCE="${1:?Wine source directory required}"
GLUON_VK_CACHE="${2:-$GLUON_VK_ROOT/build/downloads}"
GLUON_VK_COMMIT=2fa203425eb4af9dfc6b03f97ef72b0b5bcb8350
GLUON_VK_SHA=7cf3d7ec74371f4395d221c05aa23d994a5e03d0ccfb6269c60399d333551bcd
GLUON_VK_URL="https://raw.githubusercontent.com/KhronosGroup/Vulkan-Headers/$GLUON_VK_COMMIT/registry/vk.xml"
GLUON_VK_REGISTRY="$GLUON_VK_CACHE/vk-registry-$GLUON_VK_COMMIT.xml"
mkdir -p "$GLUON_VK_CACHE"
if [ ! -f "$GLUON_VK_REGISTRY" ]; then
    GLUON_VK_TEMP="$(mktemp "$GLUON_VK_CACHE/.vk-registry.XXXXXX")"
    trap 'rm -f "$GLUON_VK_TEMP"' EXIT
    curl -fL --connect-timeout 30 --max-time 120 -o "$GLUON_VK_TEMP" "$GLUON_VK_URL"
    printf '%s  %s\n' "$GLUON_VK_SHA" "$GLUON_VK_TEMP" | shasum -a 256 -c -
    mv "$GLUON_VK_TEMP" "$GLUON_VK_REGISTRY"
fi
printf '%s  %s\n' "$GLUON_VK_SHA" "$GLUON_VK_REGISTRY" | shasum -a 256 -c -
python3 "$GLUON_VK_ROOT/packaging/check-vulkan-portability.py" \
    "$GLUON_VK_SOURCE" "$GLUON_VK_REGISTRY" --applied --self-test

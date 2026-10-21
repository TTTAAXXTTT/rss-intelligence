#!/usr/bin/env bash
set -euo pipefail

version='v0.84.1'
asset='dnsproxy-linux-amd64-v0.84.1.tar.gz'
url="https://github.com/AdguardTeam/dnsproxy/releases/download/${version}/${asset}"
expected='977364197eddb5fa13c012f6f3fd61b2ce689419be8cbc2d828631da9da3e865'
stage='/tmp/rss-dnsproxy-stage'
preloaded='/tmp/rss-dnsproxy-v0.84.1.tar.gz'

rm -rf -- "$stage"
mkdir -p -- "$stage/extract"
if [ -f "$preloaded" ]; then
  cp -- "$preloaded" "$stage/$asset"
else
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --connect-timeout 20 --max-time 180 --output "$stage/$asset" "$url"
fi
actual="$(sha256sum "$stage/$asset" | awk '{print $1}')"
if [ "$actual" != "$expected" ]; then
  echo "checksum mismatch: $actual" >&2
  exit 1
fi

echo '=== verified archive ==='
printf 'version=%s\nsha256=%s\n' "$version" "$actual"
tar -tzf "$stage/$asset"

# Reject absolute paths and parent traversal before extraction.
if tar -tzf "$stage/$asset" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo 'unsafe archive member' >&2
  exit 1
fi

tar -xzf "$stage/$asset" -C "$stage/extract"
binary="$(find "$stage/extract" -type f -name dnsproxy -perm -u+x -print -quit)"
if [ -z "$binary" ]; then
  echo 'dnsproxy binary missing from archive' >&2
  exit 1
fi

echo '=== binary ==='
"$binary" --version
echo '=== relevant help ==='
"$binary" --help | grep -E -- 'listen|port|upstream|bootstrap|cache|ipv6|health|timeout|http3' | head -80
printf 'staged_binary=%s\n' "$binary"

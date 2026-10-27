#!/bin/sh
# Install apt packages. The Azure Ubuntu mirror sometimes accepts the
# connection and then sends nothing, which used to burn the whole job timeout.
set -eu
export DEBIAN_FRONTEND=noninteractive
for file in /etc/apt/sources.list /etc/apt/sources.list.d/*; do
  [ -f "$file" ] || continue
  sudo sed -i 's|azure.archive.ubuntu.com|archive.ubuntu.com|g' "$file"
done
echo "apt mirrors: $(grep -R 'archive.ubuntu.com' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null | head -n 4)"
opts="-o Acquire::Retries=3 -o Acquire::http::Timeout=15 -o Acquire::https::Timeout=15"
sudo apt-get update $opts
attempt=1
while [ "$attempt" -le 3 ]; do
  if timeout 100 sudo apt-get install -y $opts "$@"; then
    exit 0
  fi
  echo "apt install attempt ${attempt} failed"
  attempt=$((attempt + 1))
done
exit 1

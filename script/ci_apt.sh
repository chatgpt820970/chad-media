#!/bin/sh
# Install apt packages. GitHub's Azure Ubuntu mirror sometimes drips, so a
# short timeout kills a real download and the next attempt starts over.
set -eu
export DEBIAN_FRONTEND=noninteractive
sudo grep -RIl azure.archive.ubuntu.com /etc/apt | while read -r file; do
  sudo sed -i 's|azure.archive.ubuntu.com|archive.ubuntu.com|g' "$file"
done
echo "apt mirrors: $(sudo grep -R 'URIs\|archive.ubuntu.com' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null | head -n 6)"
opts="-o Acquire::Retries=3 -o Acquire::http::Timeout=120 -o Acquire::https::Timeout=120"
sudo apt-get update $opts
sudo apt-get install -y $opts "$@"

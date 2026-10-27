#!/bin/sh
# Install apt packages without hanging on the Azure Ubuntu mirror.
# A stuck connection used to hit the outer timeout and fail the job.
set -eu
if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
  sudo sed -i 's|azure.archive.ubuntu.com|archive.ubuntu.com|g' /etc/apt/sources.list.d/ubuntu.sources
fi
if [ -f /etc/apt/sources.list ]; then
  sudo sed -i 's|azure.archive.ubuntu.com|archive.ubuntu.com|g' /etc/apt/sources.list
fi
opts="-o Acquire::Retries=5 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20"
sudo apt-get update $opts
sudo apt-get install -y $opts "$@"

#!/bin/bash
set -e
mkdir -p build
cp -r src build/
tar czf release.tgz -C build .
echo "deployed"

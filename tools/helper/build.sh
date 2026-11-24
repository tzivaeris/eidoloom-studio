#!/bin/bash
# Builds studio-helper for Linux and Windows into dist/, inside the sandbox image (which has Go), so this machine needs
# no Go of its own. Needs internet the first time, for the two terminal libraries.
set -e
cd "$(dirname "$0")"
mkdir -p dist .cache
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/src" -w /src -e HOME=/src/.cache -e GOCACHE=/src/.cache/build -e GOPATH=/src/.cache/go \
  -e CGO_ENABLED=0 strata-sandbox:universal bash -lc '
    set -e
    [ -f go.sum ] || { go get github.com/creack/pty@v1.1.24 github.com/UserExistsError/conpty@v0.1.4; }
    grep -q fyne.io/systray go.mod || go get fyne.io/systray@v1.11.0
    go mod tidy
    go vet ./...
    GOOS=windows go vet ./...
    GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w -X main.version=$(cat VERSION)" -o dist/studio-helper .
    GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "-s -w -H windowsgui -X main.version=$(cat VERSION)" -o dist/studio-helper.exe .   # no console window
  '
ls -la dist

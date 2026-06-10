#!/usr/bin/env bash
# Runs in the parent process before script.sh is forked.
# Sets $port (uvicorn).

source_helpers

port=$(find_port)
export port

echo "Port — uvicorn:${port}"

# OOD renders script.sh.erb without execute permission; fix that.
chmod +x ./script.sh 2>/dev/null || true

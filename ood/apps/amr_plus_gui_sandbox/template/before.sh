#!/usr/bin/env bash
# Runs on the allocated compute node before script.sh. Allocates uvicorn's port.
source_helpers
port=$(find_port)
export port
echo "Port — uvicorn:${port} on host $(hostname)"
chmod +x ./script.sh 2>/dev/null || true

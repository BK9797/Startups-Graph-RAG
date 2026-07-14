#!/usr/bin/env bash
# run_frontend.sh
# ----------------
# Launches the Streamlit frontend with two macOS segfault mitigations:
#
#   1. OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES  — exported so every
#      child/grandchild process inherits it (fixes the ObjC fork-safety
#      crash triggered by Streamlit's component iframe server).
#
#   2. --server.fileWatcherType none            — disables the watchdog
#      subprocess (separate fork that also triggers the crash).
#
# Using `export` here (rather than VAR=val inline in Make) is intentional:
# Make's inline syntax only sets the variable for the immediate command,
# not for subprocesses spawned by that command.

export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

exec ./.venv/bin/python -m streamlit run app/frontend/streamlit_app.py \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    --server.fileWatcherType none

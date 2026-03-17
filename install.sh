#!/usr/bin/env bash
# P1S Auto-Clear - auto install script (Linux/Mac)
cd "$(dirname "$0")"
echo "Installing P1S Auto-Clear..."
python3 -m pip install --upgrade pip
pip install -e ".[preview,run_loop]"
echo ""
echo "Installation complete. Run: python3 -m p1s_autoclear"

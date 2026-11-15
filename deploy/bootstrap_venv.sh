#!/usr/bin/env bash
set -u
cd /opt/learningmachine || exit 10
rm -rf .venv
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
chown -R ubuntu:ubuntu /opt/learningmachine/.venv
echo "PIP_EXIT=$?" > /tmp/pip3.done

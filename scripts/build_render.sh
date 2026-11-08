#!/bin/sh
set -eu
# Exact optional SDK dependencies, no credential resolution or inference.
python -m pip install '.[supervised]'
python -m amodex.deployment.supervised_runtime

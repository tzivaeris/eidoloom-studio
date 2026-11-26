#!/bin/sh
# Runs the test suite and keeps a copy of the output in test.log.
cd "$(dirname "$0")" || exit 1
python3 -m unittest discover -s tests 2>&1 | tee test.log

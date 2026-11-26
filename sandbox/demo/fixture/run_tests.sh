#!/bin/bash
# Runs the tests and keeps a copy of the output in test.log.
python3 -m unittest discover -s tests -v 2>&1 | tee test.log

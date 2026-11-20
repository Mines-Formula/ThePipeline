#!/usr/bin/env bash
set -euo pipefail

# DBC updates are deployed by the main repository's GitHub Actions workflow.
# Do not pull directly into data/DBCFiles: that leaves the parent checkout dirty
# because the parent repository pins a specific submodule commit.
exit 0

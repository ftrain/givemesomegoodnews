#!/bin/bash
# Deploys no longer run from this box. They ship from the dev box:
#
#   scripts/deploy givemesomegood
#
# which pushes a commit into this checkout, applies schema.sql, seeds, rebuilds
# the site, and restarts search and admin. This box has no GitHub credentials,
# and the old `git fetch && git reset --hard origin/main` would fight that flow.
echo "deploys now ship from the dev box: scripts/deploy givemesomegood" >&2
exit 1

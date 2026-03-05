#!/usr/bin/env bash
set -euo pipefail
cd /Users/gurthang/.openclaw/workspace-mage-luz
source .venv/bin/activate
python /Users/gurthang/.openclaw/workspace-mage-luz/tools/refresh_jobs_cache.py >> /Users/gurthang/.openclaw/workspace-mage-luz/logs/jobs_refresh.log 2>&1

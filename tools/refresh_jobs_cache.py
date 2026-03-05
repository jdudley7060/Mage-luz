import sys
from pathlib import Path
ROOT = Path('/Users/gurthang/.openclaw/workspace-mage-luz')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.main import _refresh_jobs_cache_full

if __name__ == '__main__':
    _refresh_jobs_cache_full()
    print('jobs_cache_refresh_done')

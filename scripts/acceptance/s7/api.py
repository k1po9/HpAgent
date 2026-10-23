"""Dedicated acceptance API composition, with the same Work limits as Worker."""
import json
from pathlib import Path

from s7.composition import configure_governance

if __name__ == '__main__':
    configure_governance(json.loads(Path('/batch/manifest.json').read_text()))
    from web_api.__main__ import main
    main()

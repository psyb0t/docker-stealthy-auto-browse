#!/usr/bin/env python3
"""Start stealthy-auto-browse: the auto-linux-desk server with the browser plugin.

The image's entrypoint runs `python -m autolinuxdesk`, which loads the plugin
named in AUTO_LINUX_DESK_PLUGINS. This file keeps `python main.py` and
`python main.py --script <path>` working for anyone who runs them directly.
"""

import os

from autolinuxdesk.server import main

if __name__ == "__main__":
    os.environ.setdefault("AUTO_LINUX_DESK_PLUGINS", "stealthy_plugin")
    main()

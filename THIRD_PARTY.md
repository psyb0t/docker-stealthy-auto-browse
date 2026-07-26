# Third-Party Software

stealthy-auto-browse's own code (this repo) is [WTFPL](LICENSE)-licensed. The
**published Docker image**, however, bakes in a browser and browser extensions
at build time (`Dockerfile`: `RUN python -m camoufox fetch` and
`RUN python /scripts/install_extensions.py`) that ship under their own licenses.
This file lists what the published image redistributes — not dev-only
dependencies, and not anything a user fetches themselves after the box is running.

| Component | Kind | License (SPDX) | Source | Where it lives | Note |
|---|---|---|---|---|---|
| [Camoufox](https://github.com/daijro/camoufox) (custom Firefox browser binary) | fetched into image at build | `MPL-2.0` | https://github.com/daijro/camoufox | `Dockerfile` — `python -m camoufox fetch` | The Camoufox browser is a Firefox fork; Firefox/Camoufox source is MPL-2.0. Full text: [`LICENSES/MPL-2.0.txt`](LICENSES/MPL-2.0.txt). The `camoufox` PyPI wrapper itself is MIT. |
| [uBlock Origin](https://github.com/gorhill/uBlock) | Firefox extension, installed at build | `GPL-3.0-only` | https://github.com/gorhill/uBlock | `scripts/install_extensions.py` (`.xpi` downloaded + installed into Camoufox) | Full text: [`LICENSES/GPL-3.0.txt`](LICENSES/GPL-3.0.txt). Corresponding source at the URL. |
| [LocalCDN](https://codeberg.org/nobody/LocalCDN) | Firefox extension, installed at build | `MPL-2.0` | https://codeberg.org/nobody/LocalCDN | `scripts/install_extensions.py` | Full text: [`LICENSES/MPL-2.0.txt`](LICENSES/MPL-2.0.txt). |
| [ClearURLs](https://github.com/ClearURLs/Addon) | Firefox extension, installed at build | `LGPL-3.0-only` | https://github.com/ClearURLs/Addon | `scripts/install_extensions.py` | Full text: [`LICENSES/LGPL-3.0.txt`](LICENSES/LGPL-3.0.txt). Corresponding source at the URL. |
| [Consent-O-Matic](https://github.com/cavi-au/Consent-O-Matic) | Firefox extension, installed at build | `MIT` | https://github.com/cavi-au/Consent-O-Matic | `scripts/install_extensions.py` | Permissive; attribution retained via this notice + upstream. |
| [Playwright](https://github.com/microsoft/playwright) | pip dependency (image) | `Apache-2.0` | https://github.com/microsoft/playwright | `Dockerfile` (pip install) | Drives the browser; permissive. |
| [PyAutoGUI](https://github.com/asweigart/pyautogui) | pip dependency (image) | `BSD-3-Clause` | https://github.com/asweigart/pyautogui | `Dockerfile` (pip install) | OS-level input; permissive. |

The extensions and Camoufox are fetched from their upstreams at build time and
baked into the image cache under the browser user's home. Because the image
redistributes MPL-2.0 (Camoufox, LocalCDN), GPL-3.0 (uBlock Origin), and
LGPL-3.0 (ClearURLs) components, the corresponding source is available at each
component's URL above, and their license texts are included under
[`LICENSES/`](LICENSES/). None of these are linked into or combined with
stealthy-auto-browse's own code — they run as a separate browser process
(aggregation) — so the repo's own source stays WTFPL.

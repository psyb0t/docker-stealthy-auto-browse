#!/usr/bin/env python3
"""Install default browser extensions into Camoufox."""

import json
import os
import subprocess
import sys
import urllib.request

EXTENSIONS = [
    {
        "id": "uBlock0@raymondhill.net",
        "name": "uBlock Origin",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4629131/ublock_origin-1.68.0.xpi",
    },
    {
        "id": "{b86e4813-687a-43e6-ab65-0bde4ab75758}",
        "name": "LocalCDN",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4582489/localcdn_fork_of_decentraleyes-2.6.82.xpi",
    },
    {
        "id": "{74145f27-f039-47ce-a470-a662b129930a}",
        "name": "ClearURLs",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4432106/clearurls-1.27.3.xpi",
    },
    {
        "id": "gdpr@cavi.au.dk",
        "name": "Consent-O-Matic",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4515369/consent_o_matic-1.1.5.xpi",
    },
]


def get_camoufox_path() -> str:
    """Get the Camoufox installation path."""
    result = subprocess.run(
        [sys.executable, "-m", "camoufox", "path"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get camoufox path: {result.stderr}")
    return result.stdout.strip()


def main() -> None:
    camoufox_path = get_camoufox_path()
    extensions_dir = os.path.join(camoufox_path, "distribution", "extensions")
    policies_file = os.path.join(camoufox_path, "distribution", "policies.json")

    os.makedirs(extensions_dir, exist_ok=True)

    # Load an existing distribution/policies.json, or start fresh if this
    # camoufox build didn't ship a default one — its presence + layout varies
    # between browser builds (0.5.x dropped the default file, which used to
    # crash this script with FileNotFoundError). Don't assume it exists.
    if os.path.exists(policies_file):
        with open(policies_file) as f:
            policies = json.load(f)
    else:
        policies = {"policies": {}}

    policies.setdefault("policies", {})
    if "ExtensionSettings" not in policies["policies"]:
        policies["policies"]["ExtensionSettings"] = {}

    for ext in EXTENSIONS:
        ext_path = os.path.join(extensions_dir, f"{ext['id']}.xpi")

        print(f"Downloading {ext['name']}...")
        urllib.request.urlretrieve(ext["url"], ext_path)

        policies["policies"]["ExtensionSettings"][ext["id"]] = {
            "installation_mode": "force_installed",
            "install_url": f"file://{ext_path}",
        }
        print(f"Installed {ext['name']} ({ext['id']})")

    with open(policies_file, "w") as f:
        json.dump(policies, f, indent=2)

    print("Extensions installed successfully")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Install default browser extensions into Camoufox."""

import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

EXTENSION_HOST = "addons.mozilla.org"
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_USER_AGENT = "stealthy-auto-browse-extension-installer"

EXTENSIONS = [
    {
        "id": "uBlock0@raymondhill.net",
        "name": "uBlock Origin",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4629131/ublock_origin-1.68.0.xpi",
        "sha256": "5caf4abda494018841222a12156919bbdd8cad82a783c38c36b22dd642704315",
    },
    {
        "id": "{b86e4813-687a-43e6-ab65-0bde4ab75758}",
        "name": "LocalCDN",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4582489/localcdn_fork_of_decentraleyes-2.6.82.xpi",
        "sha256": "2106e0826419eb1877d99c689b9c198bd483bfffab6ab9c3242b3fad674f325c",
    },
    {
        "id": "{74145f27-f039-47ce-a470-a662b129930a}",
        "name": "ClearURLs",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4432106/clearurls-1.27.3.xpi",
        "sha256": "54926b6e4274d5935a5fc0daa6320f1d371e3d2f1a5877467ca3ab22a65c4f20",
    },
    {
        "id": "gdpr@cavi.au.dk",
        "name": "Consent-O-Matic",
        "url": "https://addons.mozilla.org/firefox/downloads/file/4515369/consent_o_matic-1.1.5.xpi",
        "sha256": "a2119abc329638d6e7af1ab4e5548a348465e02eec11de08dee0af84919923dc",
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


def download_extension(extension: dict[str, str], destination: str) -> None:
    url = extension["url"]
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != EXTENSION_HOST:
        raise ValueError(f"invalid extension URL for {extension['name']}")

    request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    # URL uses HTTPS, the Mozilla extension host, and a pinned content digest.
    with urllib.request.urlopen(  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        request,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    ) as response:
        content = response.read()

    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != extension["sha256"]:
        raise RuntimeError(f"extension checksum mismatch for {extension['name']}")

    with open(destination, "wb") as extension_file:
        extension_file.write(content)


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
        download_extension(ext, ext_path)

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

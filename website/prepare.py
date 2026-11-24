"""Copy docs/ into the Docusaurus content directory and write llms.txt.

Run from the repository root through `make docs`. Standard library only, so the
Pages workflow needs nothing but python3 and Docker on a stock runner.
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
WEBSITE = ROOT / "website"
CONTENT = WEBSITE / ".content"
ASSETS = WEBSITE / ".assets"

SITE_URL = "https://psyb0t.github.io/docker-stealthy-auto-browse/"
SITE_DESCRIPTION = (
    "Run Camoufox in Docker with HTTP and MCP control, OS-level input, "
    "persistent profiles, recording, and optional browser clusters."
)

FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
LIQUID = re.compile(r"\{%|\{\{\s*(site|page|include)\.")
REQUIRED_KEYS = ("title", "description", "sidebar_position")


def front_matter(path: Path, text: str) -> dict[str, str]:
    match = FRONT_MATTER.match(text)
    if not match:
        raise SystemExit(f"{path}: missing front matter")

    values = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            values[key.strip()] = value.strip().strip('"')

    missing = [key for key in REQUIRED_KEYS if key not in values]
    if missing:
        raise SystemExit(f"{path}: front matter lacks {', '.join(missing)}")

    if not 50 <= len(values["description"]) <= 170:
        raise SystemExit(f"{path}: description must be 50 to 170 characters")

    return values


def page_url(path: Path, values: dict[str, str]) -> str:
    slug = values.get("slug", path.stem)
    return SITE_URL + slug.strip("/") + ("/" if slug.strip("/") else "")


def main() -> int:
    pages = sorted(DOCS.glob("*.md"))
    if not pages:
        raise SystemExit(f"{DOCS}: no pages")

    if CONTENT.exists():
        shutil.rmtree(CONTENT)
    CONTENT.mkdir(parents=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

    entries = []
    for path in pages:
        text = path.read_text(encoding="utf-8")
        if LIQUID.search(text):
            raise SystemExit(f"{path}: Liquid is not supported, write plain Markdown")

        values = front_matter(path, text)
        entries.append((int(values["sidebar_position"]), values, path))
        shutil.copyfile(path, CONTENT / path.name)

    lines = ["# stealthy-auto-browse", "", f"> {SITE_DESCRIPTION}", ""]
    for _, values, path in sorted(entries, key=lambda entry: entry[0]):
        lines.append(f"- [{values['title']}]({page_url(path, values)}): {values['description']}")
    (ASSETS / "llms.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"prepared {len(pages)} pages in {CONTENT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

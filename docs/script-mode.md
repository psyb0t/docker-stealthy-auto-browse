# Script Mode (Run & Exit)

Run a YAML script at container startup — execute the steps, get results as JSON on stdout, and the container exits. No HTTP server, no long-running process. Good for CI, cron jobs, one-shot scraping, or anything where you want to automate a sequence and get the output.

## Usage

```bash
# Pipe a script in, get JSON results out
cat my-script.yaml | docker run --rm -i \
  psyb0t/stealthy-auto-browse --script > results.json

# Parameterize with environment variables
cat my-script.yaml | docker run --rm -i \
  -e TARGET_URL=https://example.com \
  psyb0t/stealthy-auto-browse --script
```

## Script Format

```yaml
name: Scrape Example
on_error: stop  # "stop" (default) or "continue"
steps:
  - action: goto
    url: ${env.TARGET_URL}
    wait_until: networkidle

  - action: sleep
    duration: 2

  - action: save_screenshot
    output_id: page_screenshot

  - action: get_text
    output_id: page_text

  - action: eval
    expression: "document.title"
    output_id: title
```

## Output JSON

The JSON printed to stdout looks like this:

```json
{
  "name": "Scrape Example",
  "success": true,
  "steps_executed": 5,
  "steps_total": 5,
  "duration": 3.42,
  "step_results": [ ... ],
  "outputs": {
    "page_screenshot": "data:image/png;base64,iVBOR...",
    "page_text": { "text": "...", "length": 1234 },
    "title": { "result": "Example Domain" }
  }
}
```

- **`outputs`** contains only steps that have an `output_id`. Screenshots are base64-encoded PNGs with a data URI prefix. Everything else is the step's `data` dict as-is.
- **`step_results`** is the full execution log of every step (action, duration, success/error).
- **Logs go to stderr**, so `> results.json` gives you clean JSON.
- **Exit code** is 0 if all steps succeed, 1 if any fail.

## Key Features

- **`output_id`** on any step collects its result into the `outputs` dict. This is how you get data out.
- **`${env.VAR_NAME}`** in any string value is replaced with the environment variable. Pass `-e VAR=value` to Docker.
- **`save_screenshot`** captures the browser viewport (or full desktop with `type: desktop`). Supports `width`, `height`, `whLargest` for resize. Can also write to disk with `path: /output/file.png` (in addition to `output_id`).
- **`on_error: continue`** keeps going past failures. **`on_error: stop`** (default) halts on the first error.
- **All HTTP API actions work as script steps** — goto, click, fill, eval, wait_for_element, etc.
- **Page loaders still fire** on `goto` if configured.

## Example: Screenshot a URL

```yaml
name: Quick Screenshot
steps:
  - action: goto
    url: ${env.URL}
    wait_until: networkidle
  - action: save_screenshot
    output_id: screenshot
    whLargest: 1024
```

```bash
cat screenshot.yaml | docker run --rm -i -e URL=https://example.com \
  psyb0t/stealthy-auto-browse --script | \
  python3 -c "import sys,json,base64; d=json.load(sys.stdin); open('out.png','wb').write(base64.b64decode(d['outputs']['screenshot'].split(',')[1]))"
```

See `scripts/example_script.yaml` in the repo for a full example.

## Example: Record a Flow

Mount `/recordings` and pair `start_recording` with `stop_recording`. Multiple pairs in a single script land multiple MP4 files; collect them from the host after the container exits.

```yaml
name: Record Search
steps:
  - action: start_recording
    mode: viewport
    fps: 20
  - action: goto
    url: ${env.URL}
    wait_until: networkidle
  - action: sleep
    duration: 2
  - action: stop_recording
    slug: search-flow
```

```bash
mkdir -p ./recordings
cat record.yaml | docker run --rm -i \
  -v ./recordings:/recordings \
  -e URL=https://example.com \
  psyb0t/stealthy-auto-browse --script
# → ./recordings/search-flow.mp4
```

Full action reference: [api.md#screen-recording](api.md#screen-recording).

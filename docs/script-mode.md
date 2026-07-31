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

## Control Flow

Alongside ordinary `action` steps, scripts can use one control node per step: `if`, `repeat`, or `while`. Control nodes are intentionally explicit and bounded; they cannot carry sibling action fields.

```yaml
steps:
  - action: goto
    url: ${env.TARGET_URL}

  - if:
      condition:
        type: element
        selector: ".cookie-banner"
        state: visible
        timeout: 2
      then:
        - action: click
          selector: ".cookie-banner button.accept"
      else:
        - action: eval
          expression: "true"
          output_id: no_cookie_banner

  - repeat:
      count: 3
      steps:
        - action: scroll
          amount: -4

  - while:
      condition:
        type: text
        text: "Load more"
      max_iterations: 5
      steps:
        - action: click
          selector: "button.load-more"
```

`if` runs `then` when its condition matches and `else` when it does not. An omitted `else` is a successful no-op. `repeat.count` must be an integer from 1 to 100. `while.max_iterations` is mandatory and has the same range; if the condition still matches after that many body executions, the control step fails instead of silently truncating the workflow. All loop bodies together are limited to 1,000 executed steps, and control/condition nesting is limited to 8 levels.

The script-level `on_error` policy applies inside every nested block. Each control node appears in `step_results` with its branch or per-iteration nested results, while top-level `steps_executed` and `steps_total` retain their existing top-level-step meaning.

### Conditions

Conditions are mappings with a `type`. `timeout` is optional (0 by default), polls for up to 60 seconds, and belongs only on the outer condition.

| Type | Required fields | Matches when |
| --- | --- | --- |
| `element` | `selector`; optional `state` | CSS selector reaches `visible` (default), `hidden`, `attached`, or `detached`. |
| `text` | `text` | The page body's visible text contains the string. |
| `url` | `matches` | The current URL matches the glob pattern, e.g. `*dashboard*`. |
| `javascript` | `expression` | The expression evaluates to the boolean `true`. Non-boolean results fail the control step. |
| `output` | `output_id` and exactly one of `equals` or `exists` | A prior `output_id` exists or its optional list `path` equals the supplied JSON value. |
| `all` / `any` | `conditions` | Every / any nested condition matches. |
| `not` | `condition` | Its nested condition does not match. |

Environment placeholders are substituted recursively, including strings in control blocks: `text: "${env.EXPECTED_TEXT}"`. Conditions use the existing page-evaluation capability; only use scripts on targets you are authorized to automate.

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

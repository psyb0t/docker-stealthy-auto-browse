---
title: Page loaders
description: Match navigation URLs to local YAML action sequences, reload loaders without restarting, and customize page preparation.
sidebar_position: 7
---

# Page loaders (URL-triggered automation)

Page loaders are like **Greasemonkey/Tampermonkey userscripts** but for the HTTP API. You define a set of actions that automatically run whenever the browser navigates to a matching URL. Instead of manually sending a sequence of commands every time you visit a site, you write it once as a YAML file and the container handles it.

Use them to remove cookie popups, dismiss overlays, wait for dynamic content, clean up pages before scraping, or handle any repetitive setup you'd otherwise do manually every time.

## How they work

1. You create YAML files that define URL patterns and a list of steps.
2. Mount those files into the container at `/loaders`.
3. Whenever `goto` navigates to a URL that matches a loader's pattern, the loader's steps run automatically instead of the default navigation.

The steps are the exact same actions as the HTTP API. Every action you can send via `POST /` (goto, eval, click, system_click, sleep, scroll, wait_for_element, etc.) works as a loader step. Same names, same parameters.

## Setup

```bash
docker run -d -p 8080:8080 -p 5900:5900 \
  -v ./my-loaders:/loaders \
  psyb0t/stealthy-auto-browse
```

Set `LOADERS_DIR` to use a different path inside the container.

## Loader YAML format

```yaml
name: Human-readable name for this loader
match:
  domain: example.com      # Exact, case-sensitive hostname match (www. is stripped)
  path_prefix: /articles   # URL path must start with this
  regex: "article/\\d+"    # Full URL must match this regex
steps:
  - action: goto            # Same actions as the HTTP API
    url: "${url}"           # ${url} is replaced with the original URL
    wait_until: networkidle
  - action: eval
    expression: "document.querySelector('.cookie-banner')?.remove()"
  - action: wait_for_element
    selector: "#main-content"
    timeout: 10
```

## Match rules

Set at least one match field. If you specify multiple fields, **all** of them must match for the loader to trigger. The loader only checks that `match` is not empty, so a misspelled key such as `domian:` leaves no field set and the loader then matches every URL:

- **`domain`**: Exact hostname. `www.` is stripped from both sides before comparing, so `domain: example.com` matches `www.example.com` too. Write it in lowercase: the URL's host is lowercased, the configured value is not.
- **`path_prefix`**: The URL path must start with this string. `path_prefix: /blog` matches `/blog`, `/blog/post-1`, `/blog/archive`, etc.
- **`regex`**: The full URL is tested against this regular expression.

## The `${url}` placeholder

In a top-level string field within a loader step, `${url}` is replaced with the original URL passed to `goto`. This substitution is not recursive into nested objects. The loader replaces navigation, so a loader without a `goto` step to `${url}` never loads the page and still reports success. Use it in a navigation field; do not interpolate an untrusted URL directly into JavaScript source.

```yaml
steps:
  - action: goto
    url: "${url}"
    wait_until: networkidle
  - action: get_page_info
```

## Practical example: clean scraping

Say you're scraping a news site that has cookie popups, newsletter modals, and lazy-loaded content. Without a loader, you'd send 5+ commands after every `goto`. With a loader:

```yaml
# loaders/news_site.yaml
name: News Site Cleanup
match:
  domain: news.example
steps:
  # Navigate with full network wait so everything loads
  - action: goto
    url: "${url}"
    wait_until: networkidle

  # Wait for the main content to be there
  - action: wait_for_element
    selector: "article"
    timeout: 10

  # Kill the cookie popup
  - action: eval
    expression: "document.querySelector('.cookie-consent')?.remove()"

  # Kill the newsletter modal
  - action: eval
    expression: "document.querySelector('.newsletter-overlay')?.remove()"

  # Scroll to trigger lazy-loaded images
  - action: scroll_to_bottom
    delay: 0.3

  # Small pause for everything to settle
  - action: sleep
    duration: 1
```

Now when you `goto` any URL on `news.example`, all of this happens automatically.

Files are reloaded from disk on every `goto` and checked in filename order. Only the first matching loader runs. Mount only YAML you trust: loaders can execute JavaScript and change page state.

A failed step stops that loader's sequence in one of two ways:

- A step that returns a failure, such as a `click` with a `role` or `element_id` target that matches nothing, stops the loader. `goto` still reports `success: true`, so inspect `data.last_result.success`.
- A step that raises, such as an `eval` whose script throws, a `wait_for_element`, `wait_for_text` or loader `goto` that times out, or a `click` or `fill` with a plain `selector` that matches nothing (after a 30 s timeout), fails the whole `goto`. Over HTTP the response is `success: false` with the Playwright error. In a script the step error is only `action dispatch failed`, with the detail in the container log.

## Response when a loader triggers

Your response includes `"loader"` so you know it fired. It has no `url` or `title`, unlike a plain `goto`:

```json
{
  "success": true,
  "data": {
    "loader": "News Site Cleanup",
    "steps_executed": 6,
    "last_result": {
      "success": true,
      "timestamp": 1234567890.456,
      "data": { "slept": 1 }
    }
  }
}
```

# Web Scout Cloud

A no-card, no-VPS Actor-style runner built with GitHub Actions, Crawlee and Playwright.

## Run an Actor

Create an issue titled:

```text
[RUN] web-scout
```

Use JSON as the issue body, for example:

```json
{
  "startUrls": ["https://example.com"],
  "maxRequestsPerCrawl": 1,
  "maxDepth": 0
}
```

Only issues opened by the repository owner are allowed to launch Actors.

The workflow runs Chromium in GitHub Actions, saves the full dataset as a workflow artifact, and posts a compact result back to the triggering issue so ChatGPT can read it through the connected GitHub app.

## Included Actor

`web-scout` accepts:

- `startUrls` or `urls`
- `maxRequestsPerCrawl` (1–200)
- `maxDepth` (0–5)
- `maxConcurrency` (1–10)
- `sameDomain` (default true)
- `includeLinks` (default false)
- `textLimit`
- `waitForSelector`

## Scheduler

Recurring Actors are defined in `config/schedules.json`. The scheduled workflow wakes hourly and runs any enabled entries that are due.

## Security

This repository is intended to be safe as a public repository:

- no credentials are stored in source
- only repository-owner `[RUN]` issues execute
- never place private tokens or confidential data in public issue bodies
- use GitHub Actions Secrets for future credentials

## Runtime

- Node.js 24
- Crawlee 3.18.1
- Playwright 1.63.0

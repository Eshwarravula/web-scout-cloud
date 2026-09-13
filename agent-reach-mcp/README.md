# Agent Reach MCP for ChatGPT

A read-only remote MCP wrapper for [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach), designed for ChatGPT custom apps.

## Exposed tools

- `search` — Exa web search through the public Exa MCP backend
- `fetch` — clean public page reading through Jina Reader
- `platform_search` — public-index discovery on Instagram, X, Reddit, LinkedIn, Facebook, Xiaohongshu, Bilibili, and YouTube
- `github_search` — public GitHub repository/issue/user search
- `youtube_transcript` — public YouTube subtitles through yt-dlp
- `capabilities` — reports what the cloud wrapper can and cannot do

All tools are read-only and idempotent. The deployment intentionally contains no social-media login cookies, Chrome sessions, or write actions.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

The Streamable HTTP MCP endpoint is `http://127.0.0.1:8000/mcp` and the health endpoint is `http://127.0.0.1:8000/health`.

## ChatGPT

Deploy this directory to a public HTTPS host, then create a custom app in ChatGPT Developer Mode pointing to:

```text
https://YOUR-HOST/mcp
```

For the first cloud version, use no authentication and do not add private tokens/cookies. Add OAuth before any future credentialed or user-specific capabilities.

## Why Instagram/Facebook are different

Agent Reach routes Instagram and Facebook through OpenCLI, which reuses a user's existing Chrome login session. That is a desktop/browser capability, not something a normal headless Render instance can safely reproduce. For true logged-in Instagram/Facebook access, use a local Agent Reach/OpenCLI bridge exposed to ChatGPT through a secure MCP tunnel rather than uploading session cookies to this public service.

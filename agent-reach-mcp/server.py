from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

SERVICE_NAME = "Agent Reach for ChatGPT"
EXA_MCP_URL = "https://mcp.exa.ai/mcp"
MAX_TEXT = 50_000
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True)

mcp = MCPServer(
    SERVICE_NAME,
    version="0.1.0",
    description=(
        "Read-only internet research tools based on Agent Reach routing: web search, "
        "web reading, public social discovery, GitHub search, and YouTube transcripts."
    ),
    instructions=(
        "Use these tools only for reading and research. The cloud deployment does not "
        "contain social-media login cookies or browser sessions. Social platform search "
        "therefore uses public web indexing unless a native backend is explicitly reported."
    ),
)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _safe_public_url(url: str) -> str:
    """Reject obviously local/private targets before sending a URL to a reader."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute http:// or https:// URL")

    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Local/private hostnames are not allowed")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and not ip.is_global:
        raise ValueError("Private, loopback, link-local, and reserved IPs are not allowed")

    return parsed.geturl()


def _youtube_url(url: str) -> str:
    clean = _safe_public_url(url)
    host = (urlparse(clean).hostname or "").lower()
    allowed = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
    if host not in allowed:
        raise ValueError("This tool accepts YouTube URLs only")
    return clean


def _text_from_mcp_result(result: types.CallToolResult) -> str:
    parts: list[str] = []
    for item in result.content:
        if isinstance(item, types.TextContent):
            parts.append(item.text)
        else:
            try:
                parts.append(json.dumps(item.model_dump(mode="json"), ensure_ascii=False))
            except Exception:
                parts.append(str(item))
    if getattr(result, "structured_content", None):
        parts.append(json.dumps(result.structured_content, ensure_ascii=False, default=str))
    return "\n".join(p for p in parts if p).strip()


async def _exa_search(query: str, num_results: int) -> str:
    count = _clamp(num_results, 1, 20)
    async with streamable_http_client(EXA_MCP_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "web_search_exa",
                arguments={"query": query, "numResults": count},
            )
    text = _text_from_mcp_result(result)
    if not text:
        return "No results returned by the Exa backend."
    return text[:MAX_TEXT]


@mcp.tool(
    title="Search the web",
    description=(
        "Search the public web using Agent Reach's Exa backend. Use for current web research, "
        "finding sources, people, companies, posts, documentation, and URLs."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def search(query: str, num_results: int = 5) -> str:
    return await _exa_search(query.strip(), num_results)


@mcp.tool(
    title="Read a web page",
    description=(
        "Read a public web page through Agent Reach's Jina Reader route and return clean text/Markdown. "
        "Does not use the user's browser session or cookies."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def fetch(url: str, max_chars: int = 30_000) -> str:
    clean = _safe_public_url(url)
    limit = _clamp(max_chars, 1_000, MAX_TEXT)
    reader_url = "https://r.jina.ai/" + clean
    headers = {"User-Agent": "agent-reach-chatgpt-mcp/0.1"}
    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(reader_url)
        response.raise_for_status()
    body = response.text
    if len(body) > limit:
        body = body[:limit] + f"\n\n[Truncated to {limit} characters]"
    return body


@mcp.tool(
    title="Search a social platform publicly",
    description=(
        "Find publicly indexed results from a named social platform. This cloud-safe tool uses web "
        "indexing, not private login cookies or a browser session. Good for discovering public profiles, "
        "posts, discussions, and candidate URLs."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def platform_search(
    platform: Literal[
        "instagram",
        "twitter",
        "reddit",
        "linkedin",
        "facebook",
        "xiaohongshu",
        "bilibili",
        "youtube",
    ],
    query: str,
    num_results: int = 5,
) -> str:
    domains = {
        "instagram": "instagram.com",
        "twitter": "x.com",
        "reddit": "reddit.com",
        "linkedin": "linkedin.com",
        "facebook": "facebook.com",
        "xiaohongshu": "xiaohongshu.com",
        "bilibili": "bilibili.com",
        "youtube": "youtube.com",
    }
    domain = domains[platform]
    return await _exa_search(f"site:{domain} {query.strip()}", num_results)


@mcp.tool(
    title="Search public GitHub",
    description=(
        "Search public GitHub repositories, issues/pull requests, or users with the official public GitHub API. "
        "No private repository access and no write actions."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def github_search(
    query: str,
    kind: Literal["repositories", "issues", "users"] = "repositories",
    limit: int = 5,
) -> str:
    count = _clamp(limit, 1, 20)
    endpoint = f"https://api.github.com/search/{kind}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "agent-reach-chatgpt-mcp/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        response = await client.get(endpoint, params={"q": query, "per_page": count})
        response.raise_for_status()
        payload = response.json()

    items = payload.get("items", [])[:count]
    simplified: list[dict[str, object]] = []
    for item in items:
        if kind == "repositories":
            simplified.append(
                {
                    "name": item.get("full_name"),
                    "url": item.get("html_url"),
                    "description": item.get("description"),
                    "stars": item.get("stargazers_count"),
                    "language": item.get("language"),
                    "updated_at": item.get("updated_at"),
                }
            )
        elif kind == "issues":
            simplified.append(
                {
                    "title": item.get("title"),
                    "url": item.get("html_url"),
                    "state": item.get("state"),
                    "updated_at": item.get("updated_at"),
                    "is_pull_request": "pull_request" in item,
                }
            )
        else:
            simplified.append(
                {
                    "login": item.get("login"),
                    "url": item.get("html_url"),
                    "type": item.get("type"),
                }
            )
    return json.dumps(
        {"total_count": payload.get("total_count"), "items": simplified},
        ensure_ascii=False,
        indent=2,
    )


def _clean_vtt(raw: str) -> str:
    output: list[str] = []
    previous = ""
    timestamp = re.compile(r"^\s*\d{2}:\d{2}(?::\d{2})?[\.,]\d{3}\s+-->\s+")
    tag = re.compile(r"<[^>]+>")
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "WEBVTT" or stripped.isdigit():
            continue
        if stripped.startswith(("Kind:", "Language:", "NOTE ")):
            continue
        if timestamp.search(stripped) or "-->" in stripped:
            continue
        stripped = tag.sub("", stripped)
        stripped = stripped.replace("&nbsp;", " ").replace("&amp;", "&")
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped and stripped != previous:
            output.append(stripped)
            previous = stripped
    return "\n".join(output)


@mcp.tool(
    title="Get a YouTube transcript",
    description=(
        "Extract available creator or auto-generated subtitles from a public YouTube video using yt-dlp. "
        "This reads subtitles only; it does not upload, comment, like, or modify anything."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
async def youtube_transcript(url: str, language: str = "en", max_chars: int = 40_000) -> str:
    clean = _youtube_url(url)
    limit = _clamp(max_chars, 2_000, MAX_TEXT)
    safe_lang = re.sub(r"[^A-Za-z0-9_-]", "", language) or "en"

    with tempfile.TemporaryDirectory(prefix="agent-reach-yt-") as temp_dir:
        output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--skip-download",
            "--write-sub",
            "--write-auto-sub",
            "--sub-langs",
            f"{safe_lang}.*,{safe_lang},en.*,en",
            "--sub-format",
            "vtt",
            "-o",
            output_template,
            clean,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return "Subtitle extraction timed out."

        files = sorted(Path(temp_dir).glob("*.vtt"))
        if not files:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[-2_000:]
            return "No usable subtitles were found for this video.\n" + detail

        preferred = next((p for p in files if f".{safe_lang}." in p.name), files[0])
        transcript = _clean_vtt(preferred.read_text(encoding="utf-8", errors="replace"))
        if not transcript:
            return "Subtitles were downloaded, but no readable transcript text was extracted."
        if len(transcript) > limit:
            transcript = transcript[:limit] + f"\n\n[Truncated to {limit} characters]"
        return transcript


@mcp.tool(
    title="Check Agent Reach cloud capabilities",
    description=(
        "Report which Agent Reach capabilities this cloud wrapper exposes. It intentionally does not reveal "
        "or use private browser cookies, social login sessions, or write actions."
    ),
    annotations=READ_ONLY,
)
def capabilities() -> dict[str, object]:
    return {
        "service": SERVICE_NAME,
        "mode": "read-only cloud wrapper",
        "agent_reach_upstream": "Panniantong/Agent-Reach",
        "available": [
            "Exa web search",
            "Jina web page reader",
            "public-index social platform search",
            "public GitHub search",
            "YouTube subtitle/transcript extraction",
        ],
        "not_configured_on_cloud": [
            "Instagram/Facebook OpenCLI browser session",
            "Twitter private cookies",
            "Reddit authenticated browser/CLI session",
            "LinkedIn authenticated MCP session",
            "write/post/comment/like actions",
        ],
    }


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": SERVICE_NAME, "version": "0.1.0"})


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )

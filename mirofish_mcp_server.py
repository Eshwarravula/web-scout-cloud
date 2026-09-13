from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server import MCPServer


MCP_PATH_TOKEN = os.environ.get("MCP_PATH_TOKEN", "").strip()
if not MCP_PATH_TOKEN:
    raise RuntimeError("MCP_PATH_TOKEN is required")

POLLINATIONS_AUTH_BASE = "https://enter.pollinations.ai"
POLLINATIONS_API_BASE = "https://gen.pollinations.ai/v1"
POLLINATIONS_DEVICE_CLIENT_ID = "pk_NgBAArhUeGvSRFba"
POLLINATIONS_SCOPE = "generate keys usage"
MIROFISH_MODEL = os.environ.get("MIROFISH_MODEL", "openai")

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "mirofish-src" / "backend"
BACKEND_URL = "http://127.0.0.1:5001"

mcp = MCPServer(
    "MiroFish for ChatGPT",
    instructions=(
        "Use these tools to run MiroFish swarm simulations. On first use, call "
        "begin_mirofish_setup and show the returned verification URL/code to the user. "
        "After the user approves it, call finish_mirofish_setup. Then create a project, "
        "wait for its graph-build task, create/prepare a simulation, and start it."
    ),
)

_model_key: str | None = None
_device_flow: dict[str, Any] | None = None
_backend_process: subprocess.Popen[Any] | None = None
_backend_lock = asyncio.Lock()


def _backend_alive() -> bool:
    return _backend_process is not None and _backend_process.poll() is None


async def _probe_backend() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{BACKEND_URL}/api/graph/project/list")
            return response.status_code < 500
    except Exception:
        return False


async def _ensure_backend() -> None:
    global _backend_process

    if await _probe_backend():
        return
    if not _model_key:
        raise RuntimeError(
            "MiroFish model access is not authorized. Call begin_mirofish_setup first, "
            "approve the browser authorization, then call finish_mirofish_setup."
        )
    if not BACKEND_DIR.exists():
        raise RuntimeError("MiroFish backend files are missing from this deployment")

    async with _backend_lock:
        if await _probe_backend():
            return

        if _backend_process is not None and _backend_process.poll() is None:
            _backend_process.terminate()
            try:
                _backend_process.wait(timeout=5)
            except Exception:
                _backend_process.kill()

        env = os.environ.copy()
        env.update(
            {
                "LLM_PROVIDER": "openai",
                "LLM_API_KEY": _model_key,
                "OPENAI_API_KEY": _model_key,
                "LLM_BASE_URL": POLLINATIONS_API_BASE,
                "LLM_MODEL_NAME": MIROFISH_MODEL,
                "GRAPH_BACKEND": "kuzu",
                "KUZU_DB_PATH": "/tmp/mirofish/kuzu_db",
                "DATA_DIR": "/tmp/mirofish/json_graphs",
                "FLASK_HOST": "127.0.0.1",
                "FLASK_PORT": "5001",
                "FLASK_DEBUG": "false",
                "CORS_ORIGINS": "http://127.0.0.1",
            }
        )
        Path("/tmp/mirofish").mkdir(parents=True, exist_ok=True)
        _backend_process = subprocess.Popen(
            [sys.executable, "run.py"],
            cwd=str(BACKEND_DIR),
            env=env,
        )

        for _ in range(90):
            if _backend_process.poll() is not None:
                raise RuntimeError(
                    f"MiroFish backend exited during startup with code {_backend_process.returncode}"
                )
            if await _probe_backend():
                return
            await asyncio.sleep(1)

        raise RuntimeError("MiroFish backend did not become ready")


async def _backend_request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: float = 600.0,
) -> dict[str, Any]:
    await _ensure_backend()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method,
            f"{BACKEND_URL}{path}",
            json=json,
            data=data,
            files=files,
        )
    try:
        payload = response.json()
    except Exception:
        payload = {"success": False, "error": response.text[:2000]}
    if response.status_code >= 400:
        raise RuntimeError(
            f"MiroFish API {method} {path} failed ({response.status_code}): "
            f"{payload.get('error', payload)}"
        )
    return payload


@mcp.tool()
async def mirofish_status() -> dict[str, Any]:
    """Check whether model authorization and the private MiroFish backend are ready."""
    return {
        "model_authorized": bool(_model_key),
        "backend_running": await _probe_backend(),
        "model": MIROFISH_MODEL,
        "storage": "local KuzuDB on the private Render instance",
        "next_step": (
            "Ready for simulations"
            if _model_key
            else "Call begin_mirofish_setup"
        ),
    }


@mcp.tool()
async def begin_mirofish_setup() -> dict[str, Any]:
    """Begin secure browser authorization for the LLM used by MiroFish. No API key is exposed to chat."""
    global _device_flow

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{POLLINATIONS_AUTH_BASE}/api/device/code",
            data={
                "client_id": POLLINATIONS_DEVICE_CLIENT_ID,
                "scope": POLLINATIONS_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    response.raise_for_status()
    code = response.json()
    _device_flow = {
        "device_code": code["device_code"],
        "user_code": code.get("user_code"),
        "verification_uri_complete": code.get("verification_uri_complete"),
        "expires_at": time.time() + int(code.get("expires_in", 600)),
        "interval": max(int(code.get("interval", 5)), 5),
    }
    return {
        "status": "authorization_required",
        "verification_url": code.get("verification_uri_complete"),
        "user_code": code.get("user_code"),
        "expires_in_seconds": int(code.get("expires_in", 600)),
        "instruction": (
            "Open verification_url, approve access, then ask ChatGPT to call "
            "finish_mirofish_setup. Do not paste any secret key into chat."
        ),
    }


@mcp.tool()
async def finish_mirofish_setup() -> dict[str, Any]:
    """Finish the browser authorization after the user approves it, then start the private MiroFish backend."""
    global _model_key, _device_flow

    if _model_key:
        await _ensure_backend()
        return {"status": "ready", "backend_running": True, "model": MIROFISH_MODEL}
    if not _device_flow:
        return {"status": "not_started", "next_step": "Call begin_mirofish_setup"}
    if time.time() >= float(_device_flow["expires_at"]):
        _device_flow = None
        return {"status": "expired", "next_step": "Call begin_mirofish_setup again"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{POLLINATIONS_AUTH_BASE}/api/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": _device_flow["device_code"],
                "client_id": POLLINATIONS_DEVICE_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    body = response.json()
    if response.status_code >= 400:
        error = body.get("error", "authorization_pending")
        if error in {"authorization_pending", "slow_down"}:
            return {
                "status": "pending",
                "message": "Browser approval has not completed yet.",
                "retry_after_seconds": int(_device_flow.get("interval", 5)) + (5 if error == "slow_down" else 0),
            }
        if error in {"expired_token", "access_denied"}:
            _device_flow = None
        return {"status": "failed", "error": error, "details": body.get("error_description")}

    token = body.get("access_token")
    if not token:
        return {"status": "failed", "error": "Authorization server returned no access token"}

    _model_key = str(token)
    _device_flow = None
    await _ensure_backend()
    return {
        "status": "ready",
        "backend_running": True,
        "model": MIROFISH_MODEL,
        "message": "MiroFish is ready. The model token remains server-side and is not returned to chat.",
    }


@mcp.tool()
async def mirofish_list_projects(limit: int = 20) -> dict[str, Any]:
    """List MiroFish projects stored on this deployment."""
    return await _backend_request("GET", f"/api/graph/project/list?limit={max(1, min(limit, 100))}")


@mcp.tool()
async def mirofish_create_project(
    source_text: str,
    simulation_requirement: str,
    project_name: str = "ChatGPT MiroFish Simulation",
    additional_context: str = "",
) -> dict[str, Any]:
    """Create a real MiroFish project from source text and generate its ontology. Then use mirofish_build_graph."""
    if not source_text.strip():
        raise ValueError("source_text cannot be empty")
    if not simulation_requirement.strip():
        raise ValueError("simulation_requirement cannot be empty")

    return await _backend_request(
        "POST",
        "/api/graph/ontology/generate",
        data={
            "simulation_requirement": simulation_requirement,
            "project_name": project_name,
            "additional_context": additional_context,
        },
        files={
            "files": (
                "chatgpt_source.md",
                source_text.encode("utf-8"),
                "text/markdown",
            )
        },
    )


@mcp.tool()
async def mirofish_build_graph(
    project_id: str,
    graph_name: str = "",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> dict[str, Any]:
    """Start MiroFish knowledge-graph construction for a project. Returns a task_id for mirofish_task_status."""
    body: dict[str, Any] = {
        "project_id": project_id,
        "chunk_size": max(100, min(chunk_size, 4000)),
        "chunk_overlap": max(0, min(chunk_overlap, 1000)),
    }
    if graph_name:
        body["graph_name"] = graph_name
    return await _backend_request("POST", "/api/graph/build", json=body)


@mcp.tool()
async def mirofish_task_status(task_id: str) -> dict[str, Any]:
    """Check an asynchronous MiroFish graph-build or preparation task."""
    return await _backend_request("GET", f"/api/graph/task/{task_id}")


@mcp.tool()
async def mirofish_create_simulation(
    project_id: str,
    enable_twitter: bool = True,
    enable_reddit: bool = True,
) -> dict[str, Any]:
    """Create a simulation after the project's graph build has completed."""
    return await _backend_request(
        "POST",
        "/api/simulation/create",
        json={
            "project_id": project_id,
            "enable_twitter": enable_twitter,
            "enable_reddit": enable_reddit,
        },
    )


@mcp.tool()
async def mirofish_prepare_simulation(
    simulation_id: str,
    use_llm_for_profiles: bool = True,
    parallel_profile_count: int = 5,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    """Prepare MiroFish agents/profiles for a simulation. Returns a preparation task_id."""
    return await _backend_request(
        "POST",
        "/api/simulation/prepare",
        json={
            "simulation_id": simulation_id,
            "use_llm_for_profiles": use_llm_for_profiles,
            "parallel_profile_count": max(1, min(parallel_profile_count, 20)),
            "force_regenerate": force_regenerate,
        },
    )


@mcp.tool()
async def mirofish_prepare_status(
    simulation_id: str,
    task_id: str = "",
) -> dict[str, Any]:
    """Check whether simulation preparation is complete."""
    body: dict[str, Any] = {"simulation_id": simulation_id}
    if task_id:
        body["task_id"] = task_id
    return await _backend_request("POST", "/api/simulation/prepare/status", json=body)


@mcp.tool()
async def mirofish_start_simulation(
    simulation_id: str,
    max_rounds: int = 10,
    platform: str = "parallel",
    enable_graph_memory_update: bool = False,
) -> dict[str, Any]:
    """Start a prepared MiroFish simulation. Platform can be parallel, twitter, or reddit."""
    if platform not in {"parallel", "twitter", "reddit"}:
        raise ValueError("platform must be parallel, twitter, or reddit")
    return await _backend_request(
        "POST",
        "/api/simulation/start",
        json={
            "simulation_id": simulation_id,
            "platform": platform,
            "max_rounds": max(1, min(max_rounds, 500)),
            "enable_graph_memory_update": enable_graph_memory_update,
        },
    )


@mcp.tool()
async def mirofish_run_status(simulation_id: str, detailed: bool = False) -> dict[str, Any]:
    """Inspect current MiroFish simulation progress and agent activity."""
    suffix = "/run-status/detail" if detailed else "/run-status"
    return await _backend_request("GET", f"/api/simulation/{simulation_id}{suffix}")


@mcp.tool()
async def mirofish_stop_simulation(simulation_id: str) -> dict[str, Any]:
    """Stop a running MiroFish simulation."""
    return await _backend_request(
        "POST",
        "/api/simulation/stop",
        json={"simulation_id": simulation_id},
    )


@mcp.tool()
async def mirofish_list_simulations(project_id: str = "") -> dict[str, Any]:
    """List stored simulations, optionally filtered by project_id."""
    path = "/api/simulation/list"
    if project_id:
        path += f"?project_id={project_id}"
    return await _backend_request("GET", path)


app = mcp.streamable_http_app(
    streamable_http_path=f"/mcp/{MCP_PATH_TOKEN}",
    json_response=True,
    stateless_http=True,
    host="0.0.0.0",
)

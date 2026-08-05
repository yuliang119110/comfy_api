from __future__ import annotations

import httpx
import logging
import asyncio
from fastapi import APIRouter, Request, Response, UploadFile, File, Form

from .balancer import Balancer
from .node_manager import NodeManager
from .task_tracker import TaskTracker

logger = logging.getLogger("comfy_gateway.proxy")
router = APIRouter()

_node_manager: NodeManager = None
_balancer: Balancer = None
_task_tracker: TaskTracker = None
_http_client: httpx.AsyncClient = None


def init_proxy(
    node_manager: NodeManager,
    balancer: Balancer,
    task_tracker: TaskTracker,
    http_client: httpx.AsyncClient,
):
    global _node_manager, _balancer, _task_tracker, _http_client
    _node_manager = node_manager
    _balancer = balancer
    _task_tracker = task_tracker
    _http_client = http_client


async def _proxy_response(resp: httpx.Response) -> Response:
    content_type = resp.headers.get("content-type", "")
    if "image" in content_type or "octet-stream" in content_type:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=content_type,
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type,
    )


@router.post("/upload/image")
async def upload_image(
    image: UploadFile = File(...),
    overwrite: str = Form("false"),
    type: str = Form("input"),
    subfolder: str = Form(""),
):
    healthy = _node_manager.healthy_nodes()
    if not healthy:
        return Response(content='{"error":"no healthy nodes"}', status_code=503)

    file_content = await image.read()
    data = {"overwrite": overwrite, "type": type, "subfolder": subfolder}

    first_done = asyncio.Event()
    first_result = {}

    async def _upload_one(node):
        try:
            files = {"image": (image.filename, file_content, image.content_type or "image/png")}
            resp = await _http_client.post(
                f"{node.base_url}/upload/image", files=files, data=data, timeout=60.0
            )
            if resp.status_code == 200:
                logger.info("Upload %s -> %s OK", image.filename, node.node_id)
                if not first_done.is_set():
                    first_result["resp"] = resp
                    first_done.set()
            else:
                logger.warning("Upload to %s returned %d", node.node_id, resp.status_code)
        except Exception as e:
            logger.warning("Upload to %s failed: %s", node.node_id, e)

    tasks = [asyncio.create_task(_upload_one(n)) for n in healthy]
    done_waiter = asyncio.create_task(first_done.wait())
    all_waiter = asyncio.gather(*tasks, return_exceptions=True)

    await asyncio.wait([done_waiter, all_waiter], return_when=asyncio.FIRST_COMPLETED)
    done_waiter.cancel()

    if "resp" not in first_result:
        await all_waiter
        if "resp" not in first_result:
            return Response(content='{"error":"upload failed on all nodes"}', status_code=502)

    return Response(
        content=first_result["resp"].content,
        status_code=first_result["resp"].status_code,
        media_type="application/json",
    )


@router.post("/prompt")
async def submit_prompt(request: Request):
    body = await request.json()
    node = _balancer.select_node()
    if not node:
        return Response(content='{"error":"no healthy nodes"}', status_code=503)

    resp = await _http_client.post(
        f"{node.base_url}/prompt", json=body, timeout=30.0
    )

    if resp.status_code == 200:
        resp_json = resp.json()
        prompt_id = resp_json.get("prompt_id")
        client_id = body.get("client_id", "unknown")
        if prompt_id:
            _task_tracker.register_task(prompt_id, node.node_id, client_id)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type="application/json",
    )


@router.get("/prompt")
async def get_queue():
    async def _fetch(node):
        resp = await _http_client.get(f"{node.base_url}/prompt", timeout=5.0)
        return resp.json() if resp.status_code == 200 else {}

    results = await asyncio.gather(
        *[_fetch(n) for n in _node_manager.healthy_nodes()],
        return_exceptions=True,
    )
    all_pending = []
    all_running = []
    for r in results:
        if isinstance(r, dict):
            all_pending.extend(r.get("queue_pending", []))
            all_running.extend(r.get("queue_running", []))
    return {"queue_pending": all_pending, "queue_running": all_running}


@router.get("/history/{prompt_id}")
async def get_history(prompt_id: str):
    node_id = _task_tracker.get_node_for_task(prompt_id)
    if node_id:
        node = _node_manager.get_node(node_id)
        if node:
            resp = await _http_client.get(
                f"{node.base_url}/history/{prompt_id}", timeout=10.0
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type="application/json",
            )

    async def _try_history(node):
        resp = await _http_client.get(
            f"{node.base_url}/history/{prompt_id}", timeout=5.0
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get(prompt_id):
                return resp
        return None

    results = await asyncio.gather(
        *[_try_history(n) for n in _node_manager.healthy_nodes()],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, httpx.Response):
            return Response(
                content=r.content,
                status_code=r.status_code,
                media_type="application/json",
            )

    return Response(content="{}", status_code=404)


@router.get("/history")
async def get_all_history(request: Request):
    params = dict(request.query_params)

    async def _fetch(node):
        resp = await _http_client.get(
            f"{node.base_url}/history", params=params, timeout=10.0
        )
        return resp.json() if resp.status_code == 200 else {}

    results = await asyncio.gather(
        *[_fetch(n) for n in _node_manager.healthy_nodes()],
        return_exceptions=True,
    )
    merged = {}
    for r in results:
        if isinstance(r, dict):
            merged.update(r)
    return merged


@router.get("/view")
async def view_file(request: Request):
    params = dict(request.query_params)
    filename = params.get("filename", "")

    node_id = _balancer.get_node_for_output(filename)
    if node_id:
        node = _node_manager.get_node(node_id)
        if node:
            resp = await _http_client.get(
                f"{node.base_url}/view", params=params, timeout=30.0
            )
            if resp.status_code == 200:
                return await _proxy_response(resp)

    async def _try_node(node):
        resp = await _http_client.get(
            f"{node.base_url}/view", params=params, timeout=10.0
        )
        if resp.status_code == 200:
            return node, resp
        return None

    results = await asyncio.gather(
        *[_try_node(n) for n in _node_manager.healthy_nodes()],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, tuple):
            node, resp = r
            _balancer.record_output(filename, node.node_id)
            return await _proxy_response(resp)

    return Response(content='{"error":"file not found"}', status_code=404)


@router.get("/system_stats")
async def system_stats():
    async def _fetch(node):
        try:
            resp = await _http_client.get(
                f"{node.base_url}/system_stats", timeout=5.0
            )
            return node.node_id, resp.json() if resp.status_code == 200 else {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return node.node_id, {"error": str(e)}

    results = await asyncio.gather(
        *[_fetch(n) for n in _node_manager.nodes.values()]
    )
    return dict(results)


@router.post("/interrupt")
async def interrupt(request: Request):
    try:
        body = await request.json()
        prompt_id = body.get("prompt_id")
    except Exception:
        prompt_id = None

    if prompt_id:
        node_id = _task_tracker.get_node_for_task(prompt_id)
        if node_id:
            node = _node_manager.get_node(node_id)
            if node:
                resp = await _http_client.post(
                    f"{node.base_url}/interrupt", timeout=5.0
                )
                return Response(content=resp.content, status_code=resp.status_code)

    for node in _node_manager.healthy_nodes():
        try:
            await _http_client.post(f"{node.base_url}/interrupt", timeout=5.0)
        except Exception:
            pass
    return Response(status_code=200)


@router.post("/queue")
async def queue_action(request: Request):
    body = await request.json()
    delete_data = body.get("delete")

    if delete_data and isinstance(delete_data, list):
        node_prompts = {}
        for prompt_id in delete_data:
            node_id = _task_tracker.get_node_for_task(prompt_id)
            if node_id:
                node_prompts.setdefault(node_id, []).append(prompt_id)

        for node_id, prompts in node_prompts.items():
            node = _node_manager.get_node(node_id)
            if node:
                try:
                    await _http_client.post(
                        f"{node.base_url}/queue",
                        json={"delete": prompts},
                        timeout=5.0,
                    )
                except Exception as e:
                    logger.warning("Queue delete failed on %s: %s", node_id, e)
        return Response(status_code=200)

    for node in _node_manager.healthy_nodes():
        try:
            await _http_client.post(
                f"{node.base_url}/queue", json=body, timeout=5.0
            )
        except Exception:
            pass
    return Response(status_code=200)

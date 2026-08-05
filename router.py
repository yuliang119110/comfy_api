from __future__ import annotations

import uuid
import logging
import httpx
import os
import asyncio
from typing import Dict, Any, Optional
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse

from .models import (
    Txt2ImgRequest, Img2ImgRequest, TxtImg2ImgRequest,
    Txt2VidRequest, Img2VidRequest, Vid2VidRequest,
    TxtImg2VidRequest, TxtImgVid2VidRequest
)
from .balancer import Balancer
from .node_manager import NodeManager
from .task_tracker import TaskTracker
from .workflow_builder import WorkflowBuilder
from .mock_router import maybe_mock_generate, set_mock_task_tracker

logger = logging.getLogger("comfy_gateway.router")
router = APIRouter(prefix="/api/generate")

_balancer: Balancer = None
_node_manager: NodeManager = None
_task_tracker: TaskTracker = None
_http_client: httpx.AsyncClient = None
_workflow_builder: WorkflowBuilder = None


def init_router(
    node_manager: NodeManager,
    balancer: Balancer,
    task_tracker: TaskTracker,
    http_client: httpx.AsyncClient,
    workflow_builder: WorkflowBuilder
):
    global _node_manager, _balancer, _task_tracker, _http_client, _workflow_builder
    _node_manager = node_manager
    _balancer = balancer
    _task_tracker = task_tracker
    _http_client = http_client
    _workflow_builder = workflow_builder
    set_mock_task_tracker(task_tracker)


async def upload_file_to_nodes(file_bytes: bytes, filename: str, content_type: str) -> str:
    healthy = _node_manager.healthy_nodes()
    if not healthy:
        raise HTTPException(503, "No healthy ComfyUI nodes available")

    # Broadcast to all nodes, return when first succeeds
    first_done = asyncio.Event()
    first_result = {}

    async def _upload_one(node):
        try:
            files = {"image": (filename, file_bytes, content_type)}
            data = {"overwrite": "true"}
            resp = await _http_client.post(f"{node.base_url}/upload/image", files=files, data=data, timeout=60.0)
            if resp.status_code == 200:
                if not first_done.is_set():
                    first_result["name"] = resp.json()["name"]
                    first_done.set()
        except Exception as e:
            logger.warning("Upload failed on %s: %s", node.node_id, e)

    tasks = [asyncio.create_task(_upload_one(n)) for n in healthy]
    try:
        await asyncio.wait_for(first_done.wait(), timeout=60.0)
    except asyncio.TimeoutError:
        pass

    if "name" not in first_result:
        raise HTTPException(502, "Failed to upload input file to any healthy ComfyUI nodes")

    return first_result["name"]


async def submit_and_track_task(workflow: Dict[str, Any], client_id: str) -> str:
    node = _balancer.select_node()
    if not node:
        raise HTTPException(503, "No healthy ComfyUI nodes available")

    payload = {"prompt": workflow, "client_id": client_id}
    resp = await _http_client.post(f"{node.base_url}/prompt", json=payload, timeout=30.0)
    if resp.status_code != 200:
        raise HTTPException(502, f"Failed to submit task to ComfyUI node {node.node_id}: {resp.text}")

    prompt_id = resp.json()["prompt_id"]
    _task_tracker.register_task(prompt_id, node.node_id, client_id)
    return prompt_id


async def poll_task_result(prompt_id: str, timeout: float = 300.0) -> list[Dict[str, Any]]:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        task = _task_tracker.get_task(prompt_id)
        if task:
            if task.status == "completed":
                # Find output files in history
                node_id = task.node_id
                node = _node_manager.get_node(node_id)
                if node:
                    resp = await _http_client.get(f"{node.base_url}/history/{prompt_id}")
                    if resp.status_code == 200:
                        outputs = resp.json().get(prompt_id, {}).get("outputs", {})
                        files = []
                        for node_out in outputs.values():
                            if "images" in node_out:
                                files.extend(node_out["images"])
                            elif "gifs" in node_out:
                                files.extend(node_out["gifs"])
                        return files
            elif task.status == "error":
                raise HTTPException(500, f"Task failed with error: {task.error}")
        
        # Fallback: check ComfyUI history
        node_id = _task_tracker.get_node_for_task(prompt_id)
        if node_id:
            node = _node_manager.get_node(node_id)
            if node:
                resp = await _http_client.get(f"{node.base_url}/history/{prompt_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    if prompt_id in data:
                        outputs = data[prompt_id].get("outputs", {})
                        files = []
                        for node_out in outputs.values():
                            if "images" in node_out:
                                files.extend(node_out["images"])
                            elif "gifs" in node_out:
                                files.extend(node_out["gifs"])
                        return files

        await asyncio.sleep(2.0)
    raise HTTPException(504, "Task generation timed out")


# 1. Text to Image
@router.post("/txt2img")
async def txt2img(req: Txt2ImgRequest):
    mock = await maybe_mock_generate("txt2img", req.model_dump())
    if mock: return mock
    client_id = f"gateway-txt2img-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("txt2img", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}


# 2. Image to Image
@router.post("/img2img")
async def img2img(
    prompt: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    seed: int = Form(-1),
    steps: int = Form(20),
    cfg: float = Form(7.0),
    width: int = Form(512),
    height: int = Form(512),
    strength: float = Form(0.7),
    negative_prompt: Optional[str] = Form(None),
    image: UploadFile = File(...)
):
    image_bytes = await image.read()
    filename = await upload_file_to_nodes(image_bytes, image.filename, image.content_type)
    
    req = Img2ImgRequest(
        prompt=prompt, model=model, seed=seed, steps=steps, cfg=cfg,
        width=width, height=height, strength=strength, negative_prompt=negative_prompt,
        image_url=filename
    )
    mock = await maybe_mock_generate("img2img", req.model_dump())
    if mock: return mock
    client_id = f"gateway-img2img-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("img2img", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}


# 3. Text + Image to Image
@router.post("/txt_img2img")
async def txt_img2img(
    prompt: str = Form(...),
    model: Optional[str] = Form(None),
    seed: int = Form(-1),
    steps: int = Form(20),
    cfg: float = Form(7.0),
    width: int = Form(512),
    height: int = Form(512),
    strength: float = Form(0.7),
    negative_prompt: Optional[str] = Form(None),
    image: UploadFile = File(...)
):
    image_bytes = await image.read()
    filename = await upload_file_to_nodes(image_bytes, image.filename, image.content_type)
    
    req = TxtImg2ImgRequest(
        prompt=prompt, model=model, seed=seed, steps=steps, cfg=cfg,
        width=width, height=height, strength=strength, negative_prompt=negative_prompt,
        image_url=filename
    )
    mock = await maybe_mock_generate("txt_img2img", req.model_dump())
    if mock: return mock
    client_id = f"gateway-txt_img2img-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("txt_img2img", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}


# 4. Text to Video
@router.post("/txt2vid")
async def txt2vid(req: Txt2VidRequest):
    mock = await maybe_mock_generate("txt2vid", req.model_dump())
    if mock: return mock
    client_id = f"gateway-txt2vid-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("txt2vid", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id, timeout=600.0)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}


# 5. Image to Video
@router.post("/img2vid")
async def img2vid(
    prompt: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    seed: int = Form(-1),
    steps: int = Form(20),
    cfg: float = Form(7.0),
    width: int = Form(512),
    height: int = Form(512),
    fps: int = Form(15),
    frames: int = Form(16),
    negative_prompt: Optional[str] = Form(None),
    image: UploadFile = File(...)
):
    image_bytes = await image.read()
    filename = await upload_file_to_nodes(image_bytes, image.filename, image.content_type)
    
    req = Img2VidRequest(
        prompt=prompt, model=model, seed=seed, steps=steps, cfg=cfg,
        width=width, height=height, fps=fps, frames=frames, negative_prompt=negative_prompt,
        image_url=filename
    )
    mock = await maybe_mock_generate("img2vid", req.model_dump())
    if mock: return mock
    client_id = f"gateway-img2vid-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("img2vid", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id, timeout=600.0)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}


# 6. Video to Video
@router.post("/vid2vid")
async def vid2vid(
    prompt: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    seed: int = Form(-1),
    steps: int = Form(20),
    cfg: float = Form(7.0),
    width: int = Form(512),
    height: int = Form(512),
    strength: float = Form(0.7),
    negative_prompt: Optional[str] = Form(None),
    video: UploadFile = File(...)
):
    video_bytes = await video.read()
    filename = await upload_file_to_nodes(video_bytes, video.filename, video.content_type)
    
    req = Vid2VidRequest(
        prompt=prompt, model=model, seed=seed, steps=steps, cfg=cfg,
        width=width, height=height, strength=strength, negative_prompt=negative_prompt,
        video_url=filename
    )
    mock = await maybe_mock_generate("vid2vid", req.model_dump())
    if mock: return mock
    client_id = f"gateway-vid2vid-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("vid2vid", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id, timeout=900.0)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}


# 7. Text + Image to Video
@router.post("/txt_img2vid")
async def txt_img2vid(
    prompt: str = Form(...),
    model: Optional[str] = Form(None),
    seed: int = Form(-1),
    steps: int = Form(20),
    cfg: float = Form(7.0),
    width: int = Form(512),
    height: int = Form(512),
    fps: int = Form(15),
    frames: int = Form(16),
    negative_prompt: Optional[str] = Form(None),
    image: UploadFile = File(...)
):
    image_bytes = await image.read()
    filename = await upload_file_to_nodes(image_bytes, image.filename, image.content_type)
    
    req = TxtImg2VidRequest(
        prompt=prompt, model=model, seed=seed, steps=steps, cfg=cfg,
        width=width, height=height, fps=fps, frames=frames, negative_prompt=negative_prompt,
        image_url=filename
    )
    mock = await maybe_mock_generate("txt_img2vid", req.model_dump())
    if mock: return mock
    client_id = f"gateway-txt_img2vid-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("txt_img2vid", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id, timeout=600.0)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}


# 8. Text + Image + Video to Video
@router.post("/txt_img_vid2vid")
async def txt_img_vid2vid(
    prompt: str = Form(...),
    model: Optional[str] = Form(None),
    seed: int = Form(-1),
    steps: int = Form(20),
    cfg: float = Form(7.0),
    width: int = Form(512),
    height: int = Form(512),
    strength: float = Form(0.7),
    negative_prompt: Optional[str] = Form(None),
    image: UploadFile = File(...),
    video: UploadFile = File(...)
):
    image_bytes = await image.read()
    image_filename = await upload_file_to_nodes(image_bytes, image.filename, image.content_type)
    
    video_bytes = await video.read()
    video_filename = await upload_file_to_nodes(video_bytes, video.filename, video.content_type)
    
    req = TxtImgVid2VidRequest(
        prompt=prompt, model=model, seed=seed, steps=steps, cfg=cfg,
        width=width, height=height, strength=strength, negative_prompt=negative_prompt,
        image_url=image_filename, video_url=video_filename
    )
    mock = await maybe_mock_generate("txt_img_vid2vid", req.model_dump())
    if mock: return mock
    client_id = f"gateway-txt_img_vid2vid-{uuid.uuid4()}"
    workflow = _workflow_builder.build_workflow("txt_img_vid2vid", req.model_dump())
    prompt_id = await submit_and_track_task(workflow, client_id)
    outputs = await poll_task_result(prompt_id, timeout=900.0)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs}

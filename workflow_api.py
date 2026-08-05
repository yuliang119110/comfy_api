from __future__ import annotations

import copy
import uuid
from typing import Any, Dict

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from .balancer import Balancer
from .models import (
    StoredWorkflowCreateRequest,
    StoredWorkflowDetail,
    StoredWorkflowRunRequest,
    StoredWorkflowRunResponse,
    StoredWorkflowSummary,
    StoredWorkflowUpdateRequest,
    WorkflowInputDescriptor,
    WorkflowInterfaceResponse,
)
from .node_manager import NodeManager
from .task_tracker import TaskTracker
from .workflow_store import WorkflowStore

router = APIRouter(prefix="/api/workflows", tags=["workflow-api"])

_node_manager: NodeManager = None
_balancer: Balancer = None
_task_tracker: TaskTracker = None
_http_client: httpx.AsyncClient = None
_workflow_store: WorkflowStore = None

FILE_INPUT_CLASS_TYPES = {"LoadImage", "LoadVideo"}
TEXT_CLASS_TYPES = {"CLIPTextEncode"}
SAMPLER_CLASS_TYPES = {"KSampler"}
LATENT_CLASS_TYPES = {"EmptyLatentImage"}
OUTPUT_CLASS_TYPES = {"SaveImage", "VHS_VideoCombine"}


def init_workflow_api(
    node_manager: NodeManager,
    balancer: Balancer,
    task_tracker: TaskTracker,
    http_client: httpx.AsyncClient,
    workflow_store: WorkflowStore,
):
    global _node_manager, _balancer, _task_tracker, _http_client, _workflow_store
    _node_manager = node_manager
    _balancer = balancer
    _task_tracker = task_tracker
    _http_client = http_client
    _workflow_store = workflow_store


async def _fetch_object_info() -> Dict[str, Any]:
    node = _balancer.select_node()
    if not node:
        return {}
    try:
        resp = await _http_client.get(f"{node.base_url}/object_info", timeout=20.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        return {}
    return {}


async def _upload_file_to_nodes(upload: UploadFile) -> str:
    healthy = _node_manager.healthy_nodes()
    if not healthy:
        raise HTTPException(503, "No healthy ComfyUI nodes available")
    content = await upload.read()
    if not content:
        raise HTTPException(400, f"Uploaded file {upload.filename} is empty")

    for node in healthy:
        files = {"image": (upload.filename, content, upload.content_type or "application/octet-stream")}
        data = {"overwrite": "true"}
        try:
            resp = await _http_client.post(f"{node.base_url}/upload/image", files=files, data=data, timeout=60.0)
            if resp.status_code == 200:
                return resp.json()["name"]
        except Exception:
            continue
    raise HTTPException(502, f"Failed to upload file {upload.filename} to ComfyUI nodes")


def _normalize_outputs(outputs: Dict[str, Any]) -> list[Dict[str, Any]]:
    normalized: list[Dict[str, Any]] = []
    for source_node_id, node_out in outputs.items():
        for output_kind in ("images", "gifs"):
            for item in node_out.get(output_kind, []):
                if not isinstance(item, dict):
                    continue
                entry = dict(item)
                entry["source_node_id"] = source_node_id
                entry["output_kind"] = output_kind[:-1] if output_kind.endswith("s") else output_kind
                filename = entry.get("filename")
                if filename and "url" not in entry:
                    entry["url"] = f"/view?filename={filename}&type={entry.get('type', 'output')}&subfolder={entry.get('subfolder', '')}"
                normalized.append(entry)
    return normalized


async def _submit_workflow(workflow: Dict[str, Any], client_id: str) -> tuple[str, str]:
    node = _balancer.select_node()
    if not node:
        raise HTTPException(503, "No healthy ComfyUI nodes available")
    payload = {"prompt": workflow, "client_id": client_id}
    resp = await _http_client.post(f"{node.base_url}/prompt", json=payload, timeout=30.0)
    if resp.status_code != 200:
        raise HTTPException(502, f"Failed to submit workflow to ComfyUI node {node.node_id}: {resp.text}")
    prompt_id = resp.json()["prompt_id"]
    _task_tracker.register_task(prompt_id, node.node_id, client_id)
    return prompt_id, node.node_id


async def _wait_for_outputs(prompt_id: str, timeout: float) -> list[Dict[str, Any]]:
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        node_id = _task_tracker.get_node_for_task(prompt_id)
        if node_id:
            node = _node_manager.get_node(node_id)
            if node:
                resp = await _http_client.get(f"{node.base_url}/history/{prompt_id}", timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if prompt_id in data:
                        outputs = data[prompt_id].get("outputs", {})
                        return _normalize_outputs(outputs)
        await asyncio.sleep(2.0)
    raise HTTPException(504, "Workflow execution timed out")


def _infer_editable_inputs(workflow: Dict[str, Any], object_info: Dict[str, Any]) -> list[WorkflowInputDescriptor]:
    descriptors: list[WorkflowInputDescriptor] = []
    for node_id, node in workflow.items():
        class_type = node.get("class_type")
        inputs = node.get("inputs") or {}
        node_info = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
        input_info = node_info.get("input", {}) if isinstance(node_info, dict) else {}
        required_info = input_info.get("required", {}) if isinstance(input_info, dict) else {}
        optional_info = input_info.get("optional", {}) if isinstance(input_info, dict) else {}
        combined_info = {**required_info, **optional_info}

        for input_name, current_value in inputs.items():
            if isinstance(current_value, list):
                continue
            descriptor = combined_info.get(input_name)
            comfy_type = None
            if isinstance(descriptor, list) and descriptor:
                comfy_type = str(descriptor[0])

            if class_type in TEXT_CLASS_TYPES or class_type in FILE_INPUT_CLASS_TYPES or class_type in SAMPLER_CLASS_TYPES or class_type in LATENT_CLASS_TYPES or class_type in OUTPUT_CLASS_TYPES or descriptor is not None:
                descriptors.append(
                    WorkflowInputDescriptor(
                        node_id=node_id,
                        class_type=class_type,
                        input_name=input_name,
                        current_value=current_value,
                        comfy_type=comfy_type,
                        required=input_name in required_info,
                    )
                )
    return descriptors


def _apply_overrides(workflow: Dict[str, Any], overrides: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    runtime = copy.deepcopy(workflow)
    for node_id, node_overrides in overrides.items():
        if node_id not in runtime:
            continue
        inputs = runtime[node_id].setdefault("inputs", {})
        for input_name, input_value in node_overrides.items():
            inputs[input_name] = input_value
    return runtime


@router.get("", response_model=list[StoredWorkflowSummary])
async def list_workflows():
    return _workflow_store.list()


@router.post("", response_model=StoredWorkflowDetail)
async def create_workflow(req: StoredWorkflowCreateRequest):
    return _workflow_store.create(req)


@router.get("/{workflow_id}", response_model=StoredWorkflowDetail)
async def get_workflow(workflow_id: str):
    if not _workflow_store.exists(workflow_id):
        raise HTTPException(404, "Workflow not found")
    return _workflow_store.get(workflow_id)


@router.put("/{workflow_id}", response_model=StoredWorkflowDetail)
async def update_workflow(workflow_id: str, req: StoredWorkflowUpdateRequest):
    if not _workflow_store.exists(workflow_id):
        raise HTTPException(404, "Workflow not found")
    return _workflow_store.update(workflow_id, req)


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str):
    if not _workflow_store.exists(workflow_id):
        raise HTTPException(404, "Workflow not found")
    _workflow_store.delete(workflow_id)
    return Response(status_code=204)


@router.get("/{workflow_id}/interface", response_model=WorkflowInterfaceResponse)
async def get_workflow_interface(workflow_id: str):
    if not _workflow_store.exists(workflow_id):
        raise HTTPException(404, "Workflow not found")
    detail = _workflow_store.get(workflow_id)
    object_info = await _fetch_object_info()
    editable_inputs = _infer_editable_inputs(detail.workflow, object_info)
    return WorkflowInterfaceResponse(
        workflow_id=detail.workflow_id,
        name=detail.name,
        editable_inputs=editable_inputs,
    )


@router.post("/{workflow_id}/run", response_model=StoredWorkflowRunResponse)
async def run_workflow(workflow_id: str, req: StoredWorkflowRunRequest):
    if not _workflow_store.exists(workflow_id):
        raise HTTPException(404, "Workflow not found")

    detail = _workflow_store.get(workflow_id)
    workflow = _apply_overrides(detail.workflow, req.input_overrides)
    client_id = req.client_id or f"workflow-run-{workflow_id}-{uuid.uuid4()}"
    prompt_id, node_id = await _submit_workflow(workflow, client_id)

    outputs = []
    status = "queued"
    if req.wait:
        outputs = await _wait_for_outputs(prompt_id, req.timeout)
        status = "completed"

    return StoredWorkflowRunResponse(
        workflow_id=workflow_id,
        prompt_id=prompt_id,
        status=status,
        node_id=node_id,
        outputs=outputs,
    )


@router.post("/{workflow_id}/run-with-files", response_model=StoredWorkflowRunResponse)
async def run_workflow_with_files(
    workflow_id: str,
    request: Request,
    wait: bool = Form(False),
    timeout: float = Form(300.0),
    client_id: str | None = Form(None),
):
    if not _workflow_store.exists(workflow_id):
        raise HTTPException(404, "Workflow not found")

    form = await request.form()
    overrides: Dict[str, Dict[str, Any]] = {}

    for key, value in form.multi_items():
        if key in {"wait", "timeout", "client_id"}:
            continue
        parts = key.split(".", 1)
        if len(parts) != 2:
            continue
        node_id, input_name = parts
        if isinstance(value, UploadFile):
            uploaded_name = await _upload_file_to_nodes(value)
            overrides.setdefault(node_id, {})[input_name] = uploaded_name
        else:
            overrides.setdefault(node_id, {})[input_name] = value

    detail = _workflow_store.get(workflow_id)
    workflow = _apply_overrides(detail.workflow, overrides)
    resolved_client_id = client_id or f"workflow-run-{workflow_id}-{uuid.uuid4()}"
    prompt_id, node_id = await _submit_workflow(workflow, resolved_client_id)

    outputs = []
    status = "queued"
    if wait:
        outputs = await _wait_for_outputs(prompt_id, timeout)
        status = "completed"

    return StoredWorkflowRunResponse(
        workflow_id=workflow_id,
        prompt_id=prompt_id,
        status=status,
        node_id=node_id,
        outputs=outputs,
    )

from __future__ import annotations

from typing import Any, Dict

import httpx
from fastapi import APIRouter

from .balancer import Balancer
from .node_manager import NodeManager

router = APIRouter(prefix="/api/comfy", tags=["comfy-introspection"])

_node_manager: NodeManager = None
_balancer: Balancer = None
_http_client: httpx.AsyncClient = None


def init_comfy_api(node_manager: NodeManager, balancer: Balancer, http_client: httpx.AsyncClient):
    global _node_manager, _balancer, _http_client
    _node_manager = node_manager
    _balancer = balancer
    _http_client = http_client


async def _get_from_selected(path: str) -> Dict[str, Any]:
    node = _balancer.select_node()
    if not node:
        return {"error": "no healthy nodes"}
    try:
        resp = await _http_client.get(f"{node.base_url}{path}", timeout=20.0)
        if resp.status_code == 200:
            return resp.json()
        return {"error": f"HTTP {resp.status_code}", "body": resp.text}
    except Exception as exc:
        return {"error": str(exc)}


def _summarize_object_info(object_info: Dict[str, Any]) -> Dict[str, Any]:
    node_types = sorted(object_info.keys()) if isinstance(object_info, dict) else []
    classes_with_file_inputs = []
    classes_with_text_inputs = []

    for class_type, definition in object_info.items():
        if not isinstance(definition, dict):
            continue
        input_data = definition.get("input", {})
        combined = {}
        if isinstance(input_data, dict):
            combined.update(input_data.get("required", {}) or {})
            combined.update(input_data.get("optional", {}) or {})
        for input_name, descriptor in combined.items():
            descriptor_type = None
            if isinstance(descriptor, list) and descriptor:
                descriptor_type = str(descriptor[0])
            if descriptor_type in {"IMAGE", "VIDEO", "STRING"}:
                if descriptor_type in {"IMAGE", "VIDEO"}:
                    classes_with_file_inputs.append({"class_type": class_type, "input_name": input_name, "type": descriptor_type})
                if descriptor_type == "STRING":
                    classes_with_text_inputs.append({"class_type": class_type, "input_name": input_name, "type": descriptor_type})

    return {
        "node_type_count": len(node_types),
        "node_types": node_types,
        "file_input_candidates": classes_with_file_inputs,
        "text_input_candidates": classes_with_text_inputs,
    }


@router.get("/capabilities")
async def capabilities():
    object_info = await _get_from_selected("/object_info")
    object_summary = _summarize_object_info(object_info) if isinstance(object_info, dict) else {}
    return {
        "proxy_endpoints": [
            "/upload/image",
            "/prompt",
            "/history",
            "/history/{prompt_id}",
            "/view",
            "/system_stats",
            "/interrupt",
            "/queue",
            "/ws",
        ],
        "comfy_introspection_endpoints": [
            "/api/comfy/capabilities",
            "/api/comfy/object_info",
            "/api/comfy/object_summary",
            "/api/comfy/system_stats",
        ],
        "node_count": len(_node_manager.nodes),
        "healthy_node_count": len(_node_manager.healthy_nodes()),
        "discovered_nodes": [node.model_dump() for node in _node_manager.nodes.values()],
        "object_info_summary": object_summary,
        "workflow_layers": {
            "standard_generation_endpoints": [
                "txt2img",
                "img2img",
                "txt_img2img",
                "txt2vid",
                "img2vid",
                "vid2vid",
                "txt_img2vid",
                "txt_img_vid2vid",
            ],
            "workflow_templates_dir": "comfy_api/templates",
            "stored_workflows_dir": "workflows",
            "independent_workflow_run_endpoints": [
                "/api/workflows/{workflow_id}/run",
                "/api/workflows/{workflow_id}/run-with-files",
            ],
        },
    }


@router.get("/object_info")
async def object_info():
    return await _get_from_selected("/object_info")


@router.get("/object_summary")
async def object_summary():
    object_info = await _get_from_selected("/object_info")
    if not isinstance(object_info, dict):
        return {"error": "object_info unavailable", "raw": object_info}
    return _summarize_object_info(object_info)


@router.get("/system_stats")
async def system_stats():
    results = {}
    for node in _node_manager.nodes.values():
        try:
            resp = await _http_client.get(f"{node.base_url}/system_stats", timeout=10.0)
            results[node.node_id] = resp.json() if resp.status_code == 200 else {"error": f"HTTP {resp.status_code}"}
        except Exception as exc:
            results[node.node_id] = {"error": str(exc)}
    return results

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from .models import StoredWorkflowCreateRequest, WorkflowInputDescriptor
from .workflow_store import WorkflowStore

router = APIRouter(prefix="/api/workflow-catalog", tags=["workflow-catalog"])

_workflow_store: WorkflowStore = None
_catalog_dirs: list[Path] = []


def init_workflow_catalog(workflow_store: WorkflowStore, catalog_dirs: list[str | Path]) -> None:
    global _workflow_store, _catalog_dirs
    _workflow_store = workflow_store
    _catalog_dirs = [Path(path) for path in catalog_dirs]


def _scan_files() -> list[Path]:
    found: list[Path] = []
    seen = set()
    for base_dir in _catalog_dirs:
        if not base_dir.exists():
            continue
        for path in base_dir.rglob("*.json"):
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(path)
    return sorted(found)


def _load_json(path: Path) -> Dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _infer_inputs_from_workflow(workflow: Dict[str, Any]) -> list[WorkflowInputDescriptor]:
    nodes = _workflow_store.get_workflow_nodes(workflow)
    result: list[WorkflowInputDescriptor] = []
    for node_id, node in nodes.items():
        class_type = node.get("class_type") or "Unknown"
        for input_name, current_value in (node.get("inputs") or {}).items():
            if isinstance(current_value, list):
                continue
            comfy_type = None
            lowered_name = str(input_name).lower()
            lowered_class = str(class_type).lower()
            if lowered_name in {"image", "images"} or "image" in lowered_class:
                comfy_type = "IMAGE"
            elif lowered_name in {"video", "videos"} or "video" in lowered_class:
                comfy_type = "VIDEO"
            elif isinstance(current_value, str):
                comfy_type = "STRING"
            elif isinstance(current_value, int):
                comfy_type = "INT"
            elif isinstance(current_value, float):
                comfy_type = "FLOAT"
            result.append(
                WorkflowInputDescriptor(
                    node_id=node_id,
                    class_type=class_type,
                    input_name=input_name,
                    current_value=current_value,
                    comfy_type=comfy_type,
                    required=None,
                )
            )
    return result


@router.get("")
async def list_catalog_workflows():
    items = []
    for path in _scan_files():
        workflow = _load_json(path)
        if workflow is None:
            continue
        fmt = _workflow_store.detect_workflow_format(workflow)
        node_count = len(workflow.get("nodes", [])) if fmt == "ui" else len(workflow)
        items.append(
            {
                "name": path.stem,
                "path": str(path),
                "format": fmt,
                "node_count": node_count,
            }
        )
    return items


@router.get("/detail")
async def get_catalog_detail(path: str):
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(404, "Workflow file not found")
    workflow = _load_json(file_path)
    if workflow is None:
        raise HTTPException(400, "Workflow file is not valid JSON")
    return {
        "name": file_path.stem,
        "path": str(file_path),
        "format": _workflow_store.detect_workflow_format(workflow),
        "editable_inputs": [item.model_dump() for item in _infer_inputs_from_workflow(workflow)],
    }


@router.post("/import")
async def import_catalog_workflow(path: str, name: str | None = None, description: str | None = None):
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(404, "Workflow file not found")
    workflow = _load_json(file_path)
    if workflow is None:
        raise HTTPException(400, "Workflow file is not valid JSON")
    detail = _workflow_store.create(
        StoredWorkflowCreateRequest(
            name=name or file_path.stem,
            description=description or f"Imported from {file_path}",
            workflow=workflow,
        )
    )
    return detail.model_dump()

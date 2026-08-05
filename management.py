from __future__ import annotations
from typing import TYPE_CHECKING
from fastapi import APIRouter, Response

if TYPE_CHECKING:
    from .node_manager import NodeManager
    from .task_tracker import TaskTracker

router = APIRouter(prefix="/router")
_node_manager = None
_task_tracker = None


def init_management(node_manager, task_tracker) -> None:
    global _node_manager, _task_tracker
    _node_manager = node_manager
    _task_tracker = task_tracker


@router.get("/nodes")
async def list_nodes():
    return [node.model_dump() for node in _node_manager.nodes.values()]


@router.get("/tasks")
async def list_tasks(limit: int = 100):
    return [t.model_dump() for t in _task_tracker.recent_tasks(limit)]


@router.get("/tasks/{prompt_id}")
async def get_task(prompt_id: str):
    task = _task_tracker.get_task(prompt_id)
    if not task:
        return Response(content='{"error":"task not found"}', status_code=404)
    return task.model_dump()


@router.post("/nodes/{node_id}/drain")
async def drain_node(node_id: str):
    if _node_manager.drain_node(node_id):
        return {"status": "draining", "node_id": node_id}
    return Response(content='{"error":"node not found"}', status_code=404)


@router.post("/nodes/{node_id}/enable")
async def enable_node(node_id: str):
    if _node_manager.enable_node(node_id):
        return {"status": "enabled", "node_id": node_id}
    return Response(content='{"error":"node not found"}', status_code=404)


@router.post("/nodes/add")
async def add_node(node_id: str, host: str, port: int = 8188):
    """Dynamically add a new ComfyUI node at runtime - no restart needed."""
    from .models import NodeInfo
    if node_id in _node_manager.nodes:
        return {"status": "exists", "node_id": node_id}
    _node_manager.nodes[node_id] = NodeInfo(node_id=node_id, host=host, port=port)
    return {"status": "added", "node_id": node_id, "host": host, "port": port}


@router.delete("/nodes/{node_id}")
async def remove_node(node_id: str):
    """Dynamically remove a ComfyUI node at runtime."""
    if node_id not in _node_manager.nodes:
        return Response(content='{"error":"node not found"}', status_code=404)
    del _node_manager.nodes[node_id]
    return {"status": "removed", "node_id": node_id}

"""
Mock short-circuit helper for comfy gateway router.
When mock mode is enabled, generation endpoints return placeholder outputs
without touching real ComfyUI workflows or models.
Tasks are still registered in the real task_tracker so /router/tasks works.
"""
from __future__ import annotations
from typing import Any, Dict

from .mock_backend import is_mock_mode, register_mock_task, get_mock_outputs
from .task_tracker import TaskTracker

_task_tracker: TaskTracker = None  # type: ignore


def set_mock_task_tracker(tracker: TaskTracker) -> None:
    global _task_tracker
    _task_tracker = tracker


async def maybe_mock_generate(task_name: str, params: Dict[str, Any]):
    """
    If mock mode is active, short-circuit the whole workflow/node pipeline
    and return placeholder outputs.  Returns None when real mode is active.
    """
    if not is_mock_mode():
        return None

    prompt_id = register_mock_task(task_name, params)

    # Also register in real task_tracker so /router/tasks shows mock tasks
    if _task_tracker is not None:
        task = _task_tracker.register_task(prompt_id, "mock-node", f"mock-client-{task_name}")
        from .models import TaskStatus
        _task_tracker.update_status(prompt_id, TaskStatus.COMPLETED)

    outputs = get_mock_outputs(prompt_id)
    return {"prompt_id": prompt_id, "status": "completed", "outputs": outputs, "mock": True}

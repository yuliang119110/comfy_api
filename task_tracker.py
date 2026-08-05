from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from .models import TaskInfo, TaskStatus

logger = logging.getLogger("comfy_gateway.task_tracker")

_MAX_COMPLETED_TASKS = 5_000


class TaskTracker:
    def __init__(self) -> None:
        self._tasks: Dict[str, TaskInfo] = {}
        self._client_tasks: Dict[str, Set[str]] = {}
        self._finished_queue: List[str] = []

    def register_task(self, prompt_id: str, node_id: str, client_id: str) -> TaskInfo:
        task = TaskInfo(
            prompt_id=prompt_id,
            node_id=node_id,
            client_id=client_id,
            created_at=datetime.now(timezone.utc),
        )
        self._tasks[prompt_id] = task
        self._client_tasks.setdefault(client_id, set()).add(prompt_id)
        logger.info("Task registered: %s -> node %s (client %s)", prompt_id, node_id, client_id)
        return task

    def get_task(self, prompt_id: str) -> Optional[TaskInfo]:
        return self._tasks.get(prompt_id)

    def get_node_for_task(self, prompt_id: str) -> Optional[str]:
        task = self._tasks.get(prompt_id)
        return task.node_id if task else None

    def get_tasks_for_client(self, client_id: str) -> List[TaskInfo]:
        prompt_ids = self._client_tasks.get(client_id, set())
        return [self._tasks[pid] for pid in prompt_ids if pid in self._tasks]

    def update_status(self, prompt_id: str, status: TaskStatus) -> None:
        task = self._tasks.get(prompt_id)
        if task:
            task.status = status
            if status in (TaskStatus.COMPLETED, TaskStatus.ERROR):
                self._finished_queue.append(prompt_id)
                self._evict_old()

    def update_progress(self, prompt_id: str, progress: dict) -> None:
        task = self._tasks.get(prompt_id)
        if task:
            task.progress = progress

    def set_error(self, prompt_id: str, error: str) -> None:
        task = self._tasks.get(prompt_id)
        if task:
            task.status = TaskStatus.ERROR
            task.error = error

    def recent_tasks(self, limit: int = 100) -> List[TaskInfo]:
        tasks = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def _evict_old(self) -> None:
        while len(self._finished_queue) > _MAX_COMPLETED_TASKS:
            old_pid = self._finished_queue.pop(0)
            old_task = self._tasks.pop(old_pid, None)
            if old_task:
                client_set = self._client_tasks.get(old_task.client_id)
                if client_set:
                    client_set.discard(old_pid)
                    if not client_set:
                        del self._client_tasks[old_task.client_id]

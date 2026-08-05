from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .models import (
    StoredWorkflowCreateRequest,
    StoredWorkflowDetail,
    StoredWorkflowSummary,
    StoredWorkflowUpdateRequest,
)


class WorkflowStore:
    def __init__(self, storage_dir: str | Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _workflow_path(self, workflow_id: str) -> Path:
        return self.storage_dir / f"{workflow_id}.json"

    def create(self, req: StoredWorkflowCreateRequest) -> StoredWorkflowDetail:
        workflow_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        record = {
            "workflow_id": workflow_id,
            "name": req.name,
            "description": req.description,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "workflow": req.workflow,
        }
        self._workflow_path(workflow_id).write_text(
            json.dumps(record, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return self.get(workflow_id)

    def list(self) -> list[StoredWorkflowSummary]:
        items: list[StoredWorkflowSummary] = []
        for path in sorted(self.storage_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            items.append(self._to_summary(data))
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items

    def get(self, workflow_id: str) -> StoredWorkflowDetail:
        path = self._workflow_path(workflow_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        return self._to_detail(data)

    def exists(self, workflow_id: str) -> bool:
        return self._workflow_path(workflow_id).exists()

    def update(self, workflow_id: str, req: StoredWorkflowUpdateRequest) -> StoredWorkflowDetail:
        path = self._workflow_path(workflow_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        if req.name is not None:
            data["name"] = req.name
        if req.description is not None:
            data["description"] = req.description
        if req.workflow is not None:
            data["workflow"] = req.workflow
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
        return self._to_detail(data)

    def delete(self, workflow_id: str) -> None:
        self._workflow_path(workflow_id).unlink(missing_ok=False)

    def build_runtime_workflow(self, workflow_id: str, overrides: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        detail = self.get(workflow_id)
        workflow = copy.deepcopy(detail.workflow)
        for node_id, node_overrides in overrides.items():
            if node_id not in workflow:
                continue
            inputs = workflow[node_id].setdefault("inputs", {})
            for input_name, input_value in node_overrides.items():
                inputs[input_name] = input_value
        return workflow

    def _to_summary(self, data: Dict[str, Any]) -> StoredWorkflowSummary:
        workflow = data.get("workflow") or {}
        return StoredWorkflowSummary(
            workflow_id=data["workflow_id"],
            name=data["name"],
            description=data.get("description"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            node_count=len(workflow),
        )

    def _to_detail(self, data: Dict[str, Any]) -> StoredWorkflowDetail:
        summary = self._to_summary(data)
        return StoredWorkflowDetail(**summary.model_dump(), workflow=data.get("workflow") or {})

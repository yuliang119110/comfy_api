from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

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

    def get_workflow_format(self, workflow_id: str) -> str:
        detail = self.get(workflow_id)
        return self.detect_workflow_format(detail.workflow)

    def detect_workflow_format(self, workflow: Dict[str, Any]) -> str:
        if isinstance(workflow, dict) and "nodes" in workflow and isinstance(workflow.get("nodes"), list):
            return "ui"
        return "api"

    def get_workflow_nodes(self, workflow: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        fmt = self.detect_workflow_format(workflow)
        if fmt == "api":
            return workflow

        nodes: Dict[str, Dict[str, Any]] = {}
        for node in workflow.get("nodes", []):
            node_id = str(node.get("id"))
            nodes[node_id] = {
                "id": node.get("id"),
                "class_type": node.get("type"),
                "inputs": self._ui_node_inputs(node),
                "_ui_node": node,
            }
        return nodes

    def build_runtime_workflow(self, workflow_id: str, overrides: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        detail = self.get(workflow_id)
        workflow = copy.deepcopy(detail.workflow)
        if self.detect_workflow_format(workflow) == "api":
            for node_id, node_overrides in overrides.items():
                if node_id not in workflow:
                    continue
                inputs = workflow[node_id].setdefault("inputs", {})
                for input_name, input_value in node_overrides.items():
                    inputs[input_name] = input_value
            return workflow

        node_lookup = {str(node.get("id")): node for node in workflow.get("nodes", [])}
        for node_id, node_overrides in overrides.items():
            node = node_lookup.get(str(node_id))
            if not node:
                continue
            widgets = node.setdefault("widgets_values", [])
            input_slots = node.get("inputs", []) or []
            for input_name, input_value in node_overrides.items():
                widget_index = self._find_ui_widget_index(node, input_name)
                if widget_index is not None:
                    while len(widgets) <= widget_index:
                        widgets.append(None)
                    widgets[widget_index] = input_value
                    continue
                for slot in input_slots:
                    if slot.get("name") == input_name and "widget" in slot:
                        widget_index = slot["widget"]
                        while len(widgets) <= widget_index:
                            widgets.append(None)
                        widgets[widget_index] = input_value
                        break
        return workflow

    def to_api_prompt(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        if self.detect_workflow_format(workflow) == "api":
            return workflow

        prompt: Dict[str, Any] = {}
        links_by_target = {}
        for link in workflow.get("links", []):
            if isinstance(link, list) and len(link) >= 6:
                _, origin_id, origin_slot, target_id, target_slot, *_ = link
            elif isinstance(link, dict):
                origin_id = link.get("origin_id")
                origin_slot = link.get("origin_slot")
                target_id = link.get("target_id")
                target_slot = link.get("target_slot")
            else:
                continue
            links_by_target[(int(target_id), int(target_slot))] = [str(origin_id), int(origin_slot)]

        for node in workflow.get("nodes", []):
            node_id = str(node.get("id"))
            inputs = self._ui_node_inputs(node)
            for slot in node.get("inputs", []) or []:
                key = (int(node.get("id")), int(slot.get("slot_index", 0)))
                if key in links_by_target:
                    inputs[slot.get("name")] = links_by_target[key]
            prompt[node_id] = {
                "class_type": node.get("type"),
                "inputs": inputs,
            }
            title = (node.get("properties") or {}).get("Node name for S&R") or node.get("title")
            if title:
                prompt[node_id]["_meta"] = {"title": title}
        return prompt

    def _ui_node_inputs(self, node: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        widgets = node.get("widgets_values") or []
        input_slots = node.get("inputs", []) or []
        for index, value in enumerate(widgets):
            input_name = self._input_name_from_widget(node, index)
            if input_name:
                result[input_name] = value
        for slot in input_slots:
            name = slot.get("name")
            widget_index = slot.get("widget")
            if name and widget_index is not None and widget_index < len(widgets):
                result.setdefault(name, widgets[widget_index])
        return result

    def _input_name_from_widget(self, node: Dict[str, Any], widget_index: int) -> str | None:
        input_slots = node.get("inputs", []) or []
        for slot in input_slots:
            if slot.get("widget") == widget_index and slot.get("name"):
                return slot.get("name")
        widgets = node.get("widgets") or []
        if widget_index < len(widgets):
            widget = widgets[widget_index]
            if isinstance(widget, dict):
                return widget.get("name")
        return None

    def _find_ui_widget_index(self, node: Dict[str, Any], input_name: str) -> int | None:
        for slot in node.get("inputs", []) or []:
            if slot.get("name") == input_name and slot.get("widget") is not None:
                return int(slot.get("widget"))
        widgets = node.get("widgets") or []
        for index, widget in enumerate(widgets):
            if isinstance(widget, dict) and widget.get("name") == input_name:
                return index
        return None

    def _to_summary(self, data: Dict[str, Any]) -> StoredWorkflowSummary:
        workflow = data.get("workflow") or {}
        node_count = len(workflow.get("nodes", [])) if self.detect_workflow_format(workflow) == "ui" else len(workflow)
        return StoredWorkflowSummary(
            workflow_id=data["workflow_id"],
            name=data["name"],
            description=data.get("description"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            node_count=node_count,
        )

    def _to_detail(self, data: Dict[str, Any]) -> StoredWorkflowDetail:
        summary = self._to_summary(data)
        return StoredWorkflowDetail(**summary.model_dump(), workflow=data.get("workflow") or {})

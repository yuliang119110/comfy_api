from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any, List, Dict

from pydantic import BaseModel, Field, computed_field


class NodeHealth(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"


class NodeInfo(BaseModel):
    node_id: str
    host: str
    port: int
    health: NodeHealth = NodeHealth.HEALTHY
    queue_pending: int = 0
    queue_running: int = 0
    consecutive_failures: int = 0

    @computed_field
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @computed_field
    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"

    @computed_field
    @property
    def queue_depth(self) -> int:
        return self.queue_pending + self.queue_running


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskInfo(BaseModel):
    prompt_id: str
    node_id: str
    client_id: str
    status: TaskStatus = TaskStatus.QUEUED
    created_at: datetime = Field(default_factory=_utcnow)
    progress: Optional[dict] = None
    error: Optional[str] = None


class BaseGenRequest(BaseModel):
    model: Optional[str] = None
    seed: int = -1
    steps: int = 20
    cfg: float = 7.0
    width: int = 512
    height: int = 512
    negative_prompt: Optional[str] = None


class Txt2ImgRequest(BaseGenRequest):
    prompt: str


class Img2ImgRequest(BaseGenRequest):
    image_url: str
    prompt: Optional[str] = None
    strength: float = 0.7


class TxtImg2ImgRequest(BaseGenRequest):
    image_url: str
    prompt: str
    strength: float = 0.7


class Txt2VidRequest(BaseGenRequest):
    prompt: str
    fps: int = 15
    frames: int = 16


class Img2VidRequest(BaseGenRequest):
    image_url: str
    prompt: Optional[str] = None
    fps: int = 15
    frames: int = 16


class Vid2VidRequest(BaseGenRequest):
    video_url: str
    prompt: Optional[str] = None
    strength: float = 0.7


class TxtImg2VidRequest(BaseGenRequest):
    image_url: str
    prompt: str
    fps: int = 15
    frames: int = 16


class TxtImgVid2VidRequest(BaseGenRequest):
    image_url: str
    video_url: str
    prompt: str
    strength: float = 0.7


class StoredWorkflowCreateRequest(BaseModel):
    name: str
    workflow: Dict[str, Any]
    description: Optional[str] = None


class StoredWorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    workflow: Optional[Dict[str, Any]] = None
    description: Optional[str] = None


class StoredWorkflowSummary(BaseModel):
    workflow_id: str
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    node_count: int = 0


class StoredWorkflowDetail(StoredWorkflowSummary):
    workflow: Dict[str, Any]


class WorkflowInputDescriptor(BaseModel):
    node_id: str
    class_type: str
    input_name: str
    current_value: Any
    comfy_type: Optional[str] = None
    required: Optional[bool] = None


class WorkflowInterfaceResponse(BaseModel):
    workflow_id: str
    name: str
    editable_inputs: List[WorkflowInputDescriptor]


class StoredWorkflowRunRequest(BaseModel):
    client_id: Optional[str] = None
    wait: bool = False
    timeout: float = 300.0
    input_overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class StoredWorkflowRunResponse(BaseModel):
    workflow_id: str
    prompt_id: str
    status: str
    node_id: Optional[str] = None
    outputs: List[Dict[str, Any]] = Field(default_factory=list)

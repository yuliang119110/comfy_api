from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .node_manager import NodeManager
from .balancer import Balancer
from .task_tracker import TaskTracker
from .ws_manager import WSManager, set_ws_manager
from .ws_manager import router as ws_router
from .workflow_builder import WorkflowBuilder
from .workflow_store import WorkflowStore
from .router import router as api_router, init_router
from .proxy import router as proxy_router, init_proxy
from .management import router as mgmt_router, init_management
from .workflow_api import router as workflow_router, init_workflow_api
from .workflow_catalog import router as workflow_catalog_router, init_workflow_catalog
from .comfy_api import router as comfy_router, init_comfy_api
from .mcp_server import mcp_server, init_mcp_server
import comfy_api.router as _router_module

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("comfy_gateway")

PACKAGE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.dirname(PACKAGE_DIR)
TEMPLATES_DIR = os.path.join(PACKAGE_DIR, "templates")
STATIC_DIR = os.path.join(PACKAGE_DIR, "static")
LOCAL_OUTPUTS_DIR = os.path.join(STATIC_DIR, "outputs")
WORKFLOW_STORAGE_DIR = os.path.join(ROOT_DIR, "workflows")
CATALOG_DIRS = [
    os.path.join(ROOT_DIR, "workflow_templates"),
    os.path.join(ROOT_DIR, "ComfyUI", "user", "default", "workflows"),
    TEMPLATES_DIR,
]
os.makedirs(LOCAL_OUTPUTS_DIR, exist_ok=True)
os.makedirs(WORKFLOW_STORAGE_DIR, exist_ok=True)
try:
    probe = os.path.join(WORKFLOW_STORAGE_DIR, ".write_probe")
    with open(probe, "w", encoding="utf-8") as _probe_f:
        _probe_f.write("ok")
    os.remove(probe)
except OSError:
    fallback = os.path.join(LOCAL_OUTPUTS_DIR, "workflows")
    os.makedirs(fallback, exist_ok=True)
    WORKFLOW_STORAGE_DIR = fallback
    logger.warning("Root workflows dir not writable, falling back to %s", WORKFLOW_STORAGE_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    logger.info("Starting ComfyUI Gateway on %s:%d with %d nodes", config.host, config.port, len(config.nodes))

    node_manager = NodeManager(config)
    balancer = Balancer(node_manager)
    task_tracker = TaskTracker()
    http_client = httpx.AsyncClient(timeout=30.0)
    workflow_builder = WorkflowBuilder(templates_dir=TEMPLATES_DIR)
    workflow_store = WorkflowStore(storage_dir=WORKFLOW_STORAGE_DIR)

    ws_manager = WSManager(node_manager, balancer, task_tracker)
    set_ws_manager(ws_manager)

    init_proxy(node_manager, balancer, task_tracker, http_client)
    init_router(node_manager, balancer, task_tracker, http_client, workflow_builder)
    init_management(node_manager, task_tracker)
    init_workflow_api(node_manager, balancer, task_tracker, http_client, workflow_store)
    init_workflow_catalog(workflow_store, CATALOG_DIRS)
    init_comfy_api(node_manager, balancer, http_client)
    init_mcp_server(_router_module)

    await node_manager.start()
    await ws_manager.start()

    logger.info("ComfyUI Gateway ready - %d nodes online", len(node_manager.healthy_nodes()))
    yield

    await ws_manager.stop()
    await node_manager.stop()
    await http_client.aclose()
    logger.info("ComfyUI Gateway stopped")


app = FastAPI(title="ComfyUI Router Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(workflow_router)
app.include_router(workflow_catalog_router)
app.include_router(comfy_router)
app.include_router(proxy_router)
app.include_router(ws_router)
app.include_router(mgmt_router)

app.mount("/outputs", StaticFiles(directory=LOCAL_OUTPUTS_DIR), name="outputs")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    return {
        "service": "comfy-gateway",
        "status": "running",
        "capabilities": {
            "standard_generation": "/api/generate/*",
            "workflow_management": "/api/workflows/*",
            "workflow_catalog": "/api/workflow-catalog/*",
            "workflow_ui": "/ui",
            "comfy_introspection": "/api/comfy/*",
            "comfy_proxy": [
                "/prompt",
                "/history",
                "/view",
                "/system_stats",
                "/interrupt",
                "/queue",
                "/upload/image",
            ],
        },
        "secrets": {
            "policy": "Use environment variables, never hardcode API keys into source files.",
            "examples": ["OPENAI_API_KEY", "CUSTOM_API_KEY", "JOYCAPTION_API_KEY"],
        },
    }


@app.get("/ui")
async def workflow_ui():
    return FileResponse(os.path.join(STATIC_DIR, "workflow_ui.html"))


@app.post("/mcp/sse")
async def mcp_sse():
    return {"status": "SSE not fully configured"}


def run():
    config = load_config()
    uvicorn.run(
        "comfy_api.main:app",
        host=config.host,
        port=config.port,
        reload=config.auto_discover.enabled and os.getenv("COMFY_RELOAD", "").lower() in {"1", "true", "yes", "on"},
    )


if __name__ == "__main__":
    run()

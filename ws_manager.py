from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, List

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .balancer import Balancer
from .node_manager import NodeManager
from .task_tracker import TaskTracker

logger = logging.getLogger("comfy_gateway.ws_manager")
router = APIRouter()


class WSManager:
    def __init__(
        self,
        node_manager: NodeManager,
        balancer: Balancer,
        task_tracker: TaskTracker,
    ) -> None:
        self._node_manager = node_manager
        self._balancer = balancer
        self._task_tracker = task_tracker
        self._clients: Dict[str, WebSocket] = {}
        self._node_conns: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._backend_tasks: List[asyncio.Task] = []

    @property
    def clients(self) -> Dict[str, WebSocket]:
        return self._clients

    def register_client(self, client_id: str, ws: WebSocket) -> None:
        self._clients[client_id] = ws

    def unregister_client(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    async def start(self) -> None:
        for node in self._node_manager.nodes.values():
            task = asyncio.create_task(self._listen_node(node.node_id))
            self._backend_tasks.append(task)
        logger.info("WSManager started, listening to %d node(s)", len(self._backend_tasks))

    async def stop(self) -> None:
        for task in self._backend_tasks:
            task.cancel()
        for task in self._backend_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        for conn in self._node_conns.values():
            await conn.close()

    async def _listen_node(self, node_id: str) -> None:
        while True:
            node = self._node_manager.get_node(node_id)
            if not node:
                return
            ws_url = f"{node.ws_url}?clientId=gateway-{node_id}"
            try:
                async with websockets.connect(ws_url) as conn:
                    self._node_conns[node_id] = conn
                    logger.info("Connected to WS: %s", ws_url)
                    async for raw_msg in conn:
                        await self._handle_node_message(node_id, raw_msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("WS connection to %s failed: %s, reconnecting...", node_id, e)
                self._node_conns.pop(node_id, None)
                await asyncio.sleep(3)

    async def _handle_node_message(self, node_id: str, raw_msg: str | bytes) -> None:
        if isinstance(raw_msg, bytes):
            from .models import TaskStatus
            for client_id, ws in list(self._clients.items()):
                tasks = self._task_tracker.get_tasks_for_client(client_id)
                if any(t.node_id == node_id and t.status == TaskStatus.RUNNING for t in tasks):
                    try:
                        await ws.send_bytes(raw_msg)
                    except Exception:
                        pass
            return

        try:
            msg = json.loads(raw_msg)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")
        msg_data = msg.get("data", {})
        from .models import TaskStatus

        if msg_type == "execution_start":
            prompt_id = msg_data.get("prompt_id")
            if prompt_id:
                self._task_tracker.update_status(prompt_id, TaskStatus.RUNNING)
                node = self._node_manager.get_node(node_id)
                if node:
                    node.queue_pending = max(0, node.queue_pending - 1)
                    node.queue_running += 1

        elif msg_type == "executing":
            prompt_id = msg_data.get("prompt_id")
            node_exec = msg_data.get("node")
            if prompt_id and node_exec is None:
                self._task_tracker.update_status(prompt_id, TaskStatus.COMPLETED)
                node = self._node_manager.get_node(node_id)
                if node:
                    node.queue_running = max(0, node.queue_running - 1)

        elif msg_type == "progress":
            prompt_id = msg_data.get("prompt_id")
            if prompt_id:
                self._task_tracker.update_progress(prompt_id, msg_data)

        elif msg_type == "execution_error":
            prompt_id = msg_data.get("prompt_id")
            if prompt_id:
                error_msg = msg_data.get("exception_message", "unknown error")
                self._task_tracker.set_error(prompt_id, error_msg)
                node = self._node_manager.get_node(node_id)
                if node:
                    node.queue_running = max(0, node.queue_running - 1)

        elif msg_type == "execution_cached":
            prompt_id = msg_data.get("prompt_id")
            if prompt_id:
                self._task_tracker.update_status(prompt_id, TaskStatus.COMPLETED)

        elif msg_type == "executed":
            prompt_id = msg_data.get("prompt_id")
            output = msg_data.get("output", {})
            if prompt_id:
                for key, val_list in output.items():
                    if isinstance(val_list, list):
                        for item in val_list:
                            if isinstance(item, dict) and "filename" in item:
                                self._balancer.record_output(item["filename"], node_id)

        prompt_id = msg_data.get("prompt_id")
        if prompt_id:
            task = self._task_tracker.get_task(prompt_id)
            if task:
                client_ws = self._clients.get(task.client_id)
                if client_ws:
                    try:
                        await client_ws.send_text(raw_msg)
                    except Exception:
                        pass
                return

        for ws in list(self._clients.values()):
            try:
                await ws.send_text(raw_msg)
            except Exception:
                pass


_ws_manager: WSManager = None  # type: ignore


def set_ws_manager(manager: WSManager) -> None:
    global _ws_manager
    _ws_manager = manager


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    client_id = ws.query_params.get("clientId", "unknown")
    await ws.accept()
    _ws_manager.register_client(client_id, ws)
    logger.info("Client connected: %s", client_id)
    try:
        while True:
            data = await ws.receive_text()
            logger.debug("Client %s sent: %s", client_id, data[:100])
    except WebSocketDisconnect:
        pass
    finally:
        _ws_manager.unregister_client(client_id)
        logger.info("Client disconnected: %s", client_id)

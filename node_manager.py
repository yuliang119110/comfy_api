from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from .config import NodeConfig, RouterConfig
from .models import NodeHealth, NodeInfo

logger = logging.getLogger("comfy_gateway.node_manager")


class NodeManager:
    def __init__(self, config: RouterConfig) -> None:
        self._config = config
        self._nodes: Dict[str, NodeInfo] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._health_task: Optional[asyncio.Task] = None
        self._boot_task: Optional[asyncio.Task] = None

        for nc in config.nodes:
            self._register_node(nc)

    @property
    def nodes(self) -> Dict[str, NodeInfo]:
        return self._nodes

    def _register_node(self, nc: NodeConfig) -> NodeInfo:
        node = self._nodes.get(nc.node_id)
        if node is None:
            node = NodeInfo(node_id=nc.node_id, host=nc.host, port=nc.port)
            self._nodes[nc.node_id] = node
        else:
            node.host = nc.host
            node.port = nc.port
        return node

    def get_node(self, node_id: str) -> Optional[NodeInfo]:
        return self._nodes.get(node_id)

    def healthy_nodes(self) -> List[NodeInfo]:
        return [n for n in self._nodes.values() if n.health == NodeHealth.HEALTHY]

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=5.0)
        await self._bootstrap_nodes()
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("NodeManager started with %d node(s)", len(self._nodes))

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        if self._boot_task:
            self._boot_task.cancel()
            try:
                await self._boot_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()

    async def _bootstrap_nodes(self) -> None:
        if not self._config.auto_discover.enabled:
            return
        await self._discover_localhost_ports()
        if self._config.auto_discover.detect_processes:
            await self._discover_process_nodes()
        if self._config.auto_discover.detect_docker:
            await self._discover_docker_nodes()
        if not self._nodes:
            await self._auto_wakeup_candidates()
            await self._discover_localhost_ports()
            if self._config.auto_discover.detect_docker:
                await self._discover_docker_nodes()

    async def _discover_localhost_ports(self) -> None:
        for port in self._config.auto_discover.localhost_ports:
            if await self._port_looks_like_comfy("127.0.0.1", port):
                node_id = f"auto-local-{port}"
                self._register_node(NodeConfig(node_id=node_id, host="127.0.0.1", port=port, source="auto-local"))

    async def _discover_process_nodes(self) -> None:
        command = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -match 'python|python3|python[.]exe' -and $_.CommandLine -match 'ComfyUI.*main.py' } | "
            "Select-Object -ExpandProperty CommandLine"
        )
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            port = self._extract_port_from_command(line)
            if port and await self._port_looks_like_comfy("127.0.0.1", port):
                node_id = f"auto-proc-{port}"
                self._register_node(NodeConfig(node_id=node_id, host="127.0.0.1", port=port, source="auto-process"))

    async def _discover_docker_nodes(self) -> None:
        command = 'docker ps -a --format "{{.ID}}|{{.Names}}|{{.Ports}}|{{.Image}}|{{.State}}"'
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) != 5:
                continue
            container_id, name, ports, image, state = parts
            host_port = self._extract_host_port_from_docker_ports(ports)
            if not host_port:
                continue
            if state != "running" and self._config.auto_discover.auto_start_docker and self._looks_like_comfy_container(name, image):
                await self._docker_start(container_id)
            if await self._port_looks_like_comfy("127.0.0.1", host_port):
                node_id = f"docker-{host_port}"
                self._register_node(NodeConfig(node_id=node_id, host="127.0.0.1", port=host_port, source="docker"))

    async def _auto_wakeup_candidates(self) -> None:
        if self._config.auto_discover.auto_start_process:
            await self._start_local_comfy_process()

    async def _start_local_comfy_process(self) -> None:
        workdir = Path(self._config.auto_discover.comfy_workdir)
        if not workdir.is_absolute():
            workdir = (Path.cwd() / workdir).resolve()
        entry = Path(self._config.auto_discover.comfy_entry)
        if not entry.is_absolute():
            entry = (Path.cwd() / entry).resolve()
        if not entry.exists():
            return
        target_port = self._config.auto_discover.localhost_ports[0]
        if await self._port_looks_like_comfy("127.0.0.1", target_port):
            return
        logger.info("Auto-starting local ComfyUI on port %d", target_port)
        self._boot_task = await asyncio.create_subprocess_exec(
            self._config.auto_discover.python_executable,
            str(entry),
            "--port",
            str(target_port),
            cwd=str(workdir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(5)

    async def _docker_start(self, container_id: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "start",
            container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        await asyncio.sleep(3)

    async def _health_loop(self) -> None:
        while True:
            if self._config.auto_discover.enabled:
                await self._discover_localhost_ports()
                if self._config.auto_discover.detect_processes:
                    await self._discover_process_nodes()
                if self._config.auto_discover.detect_docker:
                    await self._discover_docker_nodes()
            await self._check_all_nodes()
            await asyncio.sleep(self._config.health_check_interval)

    async def _check_all_nodes(self) -> None:
        tasks = [self._check_node(node) for node in self._nodes.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_node(self, node: NodeInfo) -> None:
        if node.health == NodeHealth.DRAINING:
            try:
                await self._client.get(f"{node.base_url}/system_stats")
            except Exception:
                pass
            return

        try:
            resp, queue_resp = await asyncio.gather(
                self._client.get(f"{node.base_url}/system_stats"),
                self._client.get(f"{node.base_url}/prompt"),
            )
            resp.raise_for_status()
            queue_resp.raise_for_status()
            queue_data = queue_resp.json()
            node.queue_pending = len(queue_data.get("queue_pending", []))
            node.queue_running = len(queue_data.get("queue_running", []))

            if node.health == NodeHealth.UNHEALTHY:
                logger.info("Node %s recovered", node.node_id)
            node.health = NodeHealth.HEALTHY
            node.consecutive_failures = 0

        except Exception as e:
            node.consecutive_failures += 1
            logger.warning(
                "Health check failed for %s (%d/%d): %s",
                node.node_id,
                node.consecutive_failures,
                self._config.max_consecutive_failures,
                e,
            )
            if node.consecutive_failures >= self._config.max_consecutive_failures:
                if node.health != NodeHealth.UNHEALTHY:
                    logger.error("Node %s marked UNHEALTHY", node.node_id)
                node.health = NodeHealth.UNHEALTHY

    async def _port_looks_like_comfy(self, host: str, port: int) -> bool:
        try:
            resp = await self._client.get(f"http://{host}:{port}/system_stats")
            return resp.status_code == 200
        except Exception:
            return False

    def _extract_port_from_command(self, command: str) -> Optional[int]:
        parts = command.replace("=", " ").split()
        for index, part in enumerate(parts):
            if part == "--port" and index + 1 < len(parts):
                try:
                    return int(parts[index + 1])
                except ValueError:
                    return None
        return 8188 if "main.py" in command else None

    def _extract_host_port_from_docker_ports(self, ports: str) -> Optional[int]:
        for segment in ports.split(","):
            segment = segment.strip()
            if "->8188/tcp" in segment and ":" in segment:
                host_part = segment.split("->", 1)[0]
                try:
                    return int(host_part.rsplit(":", 1)[1])
                except ValueError:
                    return None
        return None

    def _looks_like_comfy_container(self, name: str, image: str) -> bool:
        text = f"{name} {image}".lower()
        return "comfy" in text

    def drain_node(self, node_id: str) -> bool:
        node = self._nodes.get(node_id)
        if not node:
            return False
        node.health = NodeHealth.DRAINING
        logger.info("Node %s set to DRAINING", node_id)
        return True

    def enable_node(self, node_id: str) -> bool:
        node = self._nodes.get(node_id)
        if not node:
            return False
        node.health = NodeHealth.HEALTHY
        node.consecutive_failures = 0
        logger.info("Node %s re-enabled", node_id)
        return True


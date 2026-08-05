from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class NodeConfig:
    node_id: str
    host: str
    port: int
    source: str = "static"


@dataclass
class AutoDiscoverConfig:
    enabled: bool = True
    localhost_ports: list[int] = field(default_factory=lambda: [8188, 8189, 8190, 8191])
    detect_processes: bool = True
    detect_docker: bool = True
    auto_start_process: bool = True
    auto_start_docker: bool = True
    comfy_entry: str = "./ComfyUI/main.py"
    comfy_workdir: str = "./ComfyUI"
    python_executable: str = "python"


@dataclass
class RouterConfig:
    host: str = "0.0.0.0"
    port: int = 8199
    health_check_interval: int = 10
    max_consecutive_failures: int = 3
    storage_mode: str = "local"
    local_output_dir: str = "./static/outputs"
    nodes: list[NodeConfig] = field(default_factory=list)
    mock_mode: bool = False
    auto_discover: AutoDiscoverConfig = field(default_factory=AutoDiscoverConfig)


def _parse_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_ports(value: object, default: list[int]) -> list[int]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return [int(item) for item in value]
    return [int(item.strip()) for item in str(value).split(",") if item.strip()]


def load_config(config_path: str | None = None) -> RouterConfig:
    cfg = RouterConfig()
    if config_path is None:
        config_path = os.getenv("COMFY_ROUTER_CONFIG", "config.yaml")

    path = Path(config_path)
    if not path.exists():
        path = Path(__file__).parent / config_path

    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        router_data = data.get("router", {})
        cfg.host = router_data.get("host", cfg.host)
        cfg.port = router_data.get("port", cfg.port)
        cfg.health_check_interval = router_data.get("health_check_interval", cfg.health_check_interval)
        cfg.max_consecutive_failures = router_data.get("max_consecutive_failures", cfg.max_consecutive_failures)
        cfg.storage_mode = router_data.get("storage_mode", cfg.storage_mode)
        cfg.local_output_dir = router_data.get("local_output_dir", cfg.local_output_dir)
        cfg.mock_mode = router_data.get("mock_mode", cfg.mock_mode)

        auto_data = data.get("auto_discover", {})
        cfg.auto_discover.enabled = _parse_bool(auto_data.get("enabled"), cfg.auto_discover.enabled)
        cfg.auto_discover.localhost_ports = _parse_ports(auto_data.get("localhost_ports"), cfg.auto_discover.localhost_ports)
        cfg.auto_discover.detect_processes = _parse_bool(auto_data.get("detect_processes"), cfg.auto_discover.detect_processes)
        cfg.auto_discover.detect_docker = _parse_bool(auto_data.get("detect_docker"), cfg.auto_discover.detect_docker)
        cfg.auto_discover.auto_start_process = _parse_bool(auto_data.get("auto_start_process"), cfg.auto_discover.auto_start_process)
        cfg.auto_discover.auto_start_docker = _parse_bool(auto_data.get("auto_start_docker"), cfg.auto_discover.auto_start_docker)
        cfg.auto_discover.comfy_entry = auto_data.get("comfy_entry", cfg.auto_discover.comfy_entry)
        cfg.auto_discover.comfy_workdir = auto_data.get("comfy_workdir", cfg.auto_discover.comfy_workdir)
        cfg.auto_discover.python_executable = auto_data.get("python_executable", cfg.auto_discover.python_executable)

        for node_data in data.get("nodes", []):
            cfg.nodes.append(
                NodeConfig(
                    node_id=node_data["node_id"],
                    host=node_data["host"],
                    port=node_data["port"],
                    source=node_data.get("source", "static"),
                )
            )

    env_nodes = os.getenv("COMFY_NODES")
    if env_nodes:
        cfg.nodes = []
        for i, entry in enumerate(env_nodes.split(",")):
            entry = entry.strip()
            if ":" in entry:
                host, port_str = entry.rsplit(":", 1)
                port = int(port_str)
            else:
                host = entry
                port = 8188
            cfg.nodes.append(NodeConfig(node_id=f"gpu-{i}", host=host, port=port, source="env"))

    if env_port := os.getenv("COMFY_ROUTER_PORT"):
        cfg.port = int(env_port)

    if env_detect := os.getenv("COMFY_AUTO_DISCOVER"):
        cfg.auto_discover.enabled = _parse_bool(env_detect, cfg.auto_discover.enabled)
    if env_ports := os.getenv("COMFY_DISCOVERY_PORTS"):
        cfg.auto_discover.localhost_ports = _parse_ports(env_ports, cfg.auto_discover.localhost_ports)

    return cfg

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Optional

from .models import NodeInfo
from .node_manager import NodeManager

logger = logging.getLogger("comfy_gateway.balancer")

_OUTPUT_AFFINITY_MAX = 10_000
_LOCAL_PENDING_TTL = 30.0


class Balancer:
    def __init__(self, node_manager: NodeManager) -> None:
        self._node_manager = node_manager
        self._output_affinity: OrderedDict[str, str] = OrderedDict()
        self._local_pending: dict[str, list[float]] = {}

    def _effective_depth(self, node: NodeInfo) -> int:
        now = time.monotonic()
        stamps = self._local_pending.get(node.node_id)
        if stamps:
            stamps[:] = [t for t in stamps if now - t < _LOCAL_PENDING_TTL]
            return node.queue_depth + len(stamps)
        return node.queue_depth

    def select_node(self) -> Optional[NodeInfo]:
        healthy = self._node_manager.healthy_nodes()
        if not healthy:
            return None
        node = min(healthy, key=self._effective_depth)
        self._local_pending.setdefault(node.node_id, []).append(time.monotonic())
        logger.info(
            "Selected %s (remote_depth=%d, local_pending=%d)",
            node.node_id,
            node.queue_depth,
            len(self._local_pending.get(node.node_id, [])),
        )
        return node

    def record_output(self, filename: str, node_id: str) -> None:
        self._output_affinity[filename] = node_id
        self._output_affinity.move_to_end(filename)
        while len(self._output_affinity) > _OUTPUT_AFFINITY_MAX:
            self._output_affinity.popitem(last=False)

    def get_node_for_output(self, filename: str) -> Optional[str]:
        return self._output_affinity.get(filename)

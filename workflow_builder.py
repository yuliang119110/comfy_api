from __future__ import annotations

import os
import json
import logging
import random
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("comfy_gateway.workflow_builder")


class WorkflowBuilder:
    def __init__(self, templates_dir: str | Path) -> None:
        self.templates_dir = Path(templates_dir)

    def _load_template_json(self, task_name: str, model_name: Optional[str] = None) -> Dict[str, Any]:
        filename = f"{task_name}.json"
        if model_name:
            specific_filename = f"{task_name}_{model_name}.json"
            specific_path = self.templates_dir / specific_filename
            if specific_path.exists():
                with open(specific_path, encoding="utf-8") as f:
                    return json.load(f)
        
        path = self.templates_dir / filename
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        
        return self._get_fallback_template(task_name)

    def _get_fallback_template(self, task_name: str) -> Dict[str, Any]:
        logger.info("Using version-agnostic fallback template for %s", task_name)
        if "vid" in task_name or "video" in task_name:
            # Fallback workflow using VHS_VideoCombine and LoadVideo
            # Node 3: KSampler, Node 4: CLIPTextEncode (prompt), Node 5: EmptyLatentImage or LoadVideo,
            # Node 6: VAEDecode, Node 7: VHS_VideoCombine or SaveImage
            return {
                "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["1", 0], "positive": ["4", 0], "negative": ["8", 0], "latent_image": ["5", 0]}},
                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "vivid_model.safetensors"}},
                "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "positive prompt", "clip": ["1", 1]}},
                "8": {"class_type": "CLIPTextEncode", "inputs": {"text": "negative prompt", "clip": ["1", 1]}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 16}},
                "6": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["1", 2]}},
                "7": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["6", 0], "frame_rate": 15, "loop_count": 0, "filename_prefix": "AnimateDiff", "format": "video/h264-mp4"}},
                "10": {"class_type": "LoadVideo", "inputs": {"video": "input.mp4"}},
                "11": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}
            }
        else:
            # Fallback workflow for Image tasks
            return {
                "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["1", 0], "positive": ["4", 0], "negative": ["8", 0], "latent_image": ["5", 0]}},
                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
                "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "positive prompt", "clip": ["1", 1]}},
                "8": {"class_type": "CLIPTextEncode", "inputs": {"text": "negative prompt", "clip": ["1", 1]}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
                "6": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["1", 2]}},
                "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "ComfyUI"}},
                "10": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}
            }

    def _locate_node_by_class_or_title(self, workflow: Dict[str, Any], class_type: str, title: Optional[str] = None) -> Optional[str]:
        for node_id, node in workflow.items():
            if title and node.get("_meta", {}).get("title") == title:
                return node_id
            if node.get("class_type") == class_type:
                return node_id
        return None

    def _locate_all_nodes_by_class(self, workflow: Dict[str, Any], class_type: str) -> list[str]:
        return [node_id for node_id, node in workflow.items() if node.get("class_type") == class_type]

    def build_workflow(self, task_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        workflow = self._load_template_json(task_name, params.get("model"))
        
        # 1. Update Checkpoint model if specified
        if params.get("model"):
            ckpt_nodes = self._locate_all_nodes_by_class(workflow, "CheckpointLoaderSimple")
            for node_id in ckpt_nodes:
                workflow[node_id]["inputs"]["ckpt_name"] = params["model"]

        # 2. Update Prompt (find positive CLIPTextEncode)
        # Often positive is the first one or contains text. Negative CLIPTextEncode usually contains negative keywords
        clip_nodes = self._locate_all_nodes_by_class(workflow, "CLIPTextEncode")
        if clip_nodes:
            # If multiple, let's check negative prompt
            if len(clip_nodes) >= 2:
                # The first one is positive
                workflow[clip_nodes[0]]["inputs"]["text"] = params.get("prompt") or ""
                # The second one is negative
                workflow[clip_nodes[1]]["inputs"]["text"] = params.get("negative_prompt") or ""
            else:
                workflow[clip_nodes[0]]["inputs"]["text"] = params.get("prompt") or ""

        # 3. Update Seed, Steps, CFG in KSampler
        sampler_nodes = self._locate_all_nodes_by_class(workflow, "KSampler")
        for node_id in sampler_nodes:
            actual_seed = params.get("seed", -1)
            if actual_seed == -1:
                actual_seed = random.randint(0, 2**53)
            
            inputs = workflow[node_id]["inputs"]
            if "seed" in inputs:
                inputs["seed"] = actual_seed
            elif "noise_seed" in inputs:
                inputs["noise_seed"] = actual_seed

            inputs["steps"] = params.get("steps", 20)
            inputs["cfg"] = params.get("cfg", 7.0)
            
            if "strength" in params:
                inputs["denoise"] = params["strength"]

        # 4. Update Latent Resolution
        latent_nodes = self._locate_all_nodes_by_class(workflow, "EmptyLatentImage")
        for node_id in latent_nodes:
            workflow[node_id]["inputs"]["width"] = params.get("width", 512)
            workflow[node_id]["inputs"]["height"] = params.get("height", 512)

        # 5. Inject Input image
        if params.get("image_url"):
            load_image_nodes = self._locate_all_nodes_by_class(workflow, "LoadImage")
            for node_id in load_image_nodes:
                workflow[node_id]["inputs"]["image"] = params["image_url"]

        # 6. Inject Input video
        if params.get("video_url"):
            load_video_nodes = self._locate_all_nodes_by_class(workflow, "LoadVideo")
            for node_id in load_video_nodes:
                workflow[node_id]["inputs"]["video"] = params["video_url"]

        # 7. Inject Video specific settings
        if "fps" in params or "frames" in params:
            # Try to update VHS_VideoCombine or empty latent batch_size
            combine_nodes = self._locate_all_nodes_by_class(workflow, "VHS_VideoCombine")
            for node_id in combine_nodes:
                if "fps" in params:
                    workflow[node_id]["inputs"]["frame_rate"] = params["fps"]
            
            latent_nodes = self._locate_all_nodes_by_class(workflow, "EmptyLatentImage")
            for node_id in latent_nodes:
                if "frames" in params:
                    workflow[node_id]["inputs"]["batch_size"] = params["frames"]

        return workflow

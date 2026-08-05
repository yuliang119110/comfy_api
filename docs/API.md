# ComfyUI API Gateway — API Reference

Base URL: `http://localhost:8199`

---

## Multi-Modal Generation Endpoints

All generation endpoints support these common parameters unless noted:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | null | Checkpoint filename (e.g. `sd_xl_base_1.0.safetensors`) |
| `seed` | int | -1 | Random seed (-1 = random) |
| `steps` | int | 20 | Diffusion steps |
| `cfg` | float | 7.0 | Classifier-free guidance scale |
| `width` | int | 512 | Output width (px) |
| `height` | int | 512 | Output height (px) |
| `negative_prompt` | string | null | Negative prompt keywords |

Response format (all endpoints):
```json
{
  "prompt_id": "uuid",
  "status": "completed",
  "outputs": [
    {"filename": "abc.png", "subfolder": "", "type": "output", "url": "/outputs/abc.png"}
  ],
  "mock": true    // only present in mock mode
}
```

---

### 1. Text → Image
**POST** `/api/generate/txt2img`

Content-Type: `application/json`

```json
{
  "prompt": "a beautiful sunset over mountains, photorealistic",
  "negative_prompt": "blurry, watermark",
  "model": "sd_xl_base_1.0.safetensors",
  "width": 1024, "height": 1024,
  "steps": 30, "cfg": 7.5, "seed": 42
}
```

---

### 2. Image → Image
**POST** `/api/generate/img2img`

Content-Type: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | file | Yes | Input image (PNG/JPG) |
| `prompt` | string | No | Text guidance |
| `strength` | float | No (0.7) | Denoising strength (0-1) |
| + common params | | | |

---

### 3. Text + Image → Image
**POST** `/api/generate/txt_img2img`

Content-Type: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | file | Yes | Reference image |
| `prompt` | string | Yes | Text description |
| `strength` | float | No (0.7) | Blend strength |
| + common params | | | |

---

### 4. Text → Video
**POST** `/api/generate/txt2vid`

Content-Type: `application/json`

```json
{
  "prompt": "a cat walking in a garden, cinematic, slow motion",
  "fps": 15,
  "frames": 16,
  "steps": 20, "cfg": 7.0
}
```

| Extra Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `fps` | int | 15 | Output frames per second |
| `frames` | int | 16 | Total number of frames |

---

### 5. Image → Video
**POST** `/api/generate/img2vid`

Content-Type: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | file | Yes | Starting frame image |
| `fps` | int | No (15) | Output FPS |
| `frames` | int | No (16) | Frame count |
| + common params | | | |

---

### 6. Video → Video
**POST** `/api/generate/vid2vid`

Content-Type: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `video` | file | Yes | Input video (MP4/WebM) |
| `prompt` | string | No | Style guidance |
| `strength` | float | No (0.7) | Transform strength |
| + common params | | | |

---

### 7. Text + Image → Video
**POST** `/api/generate/txt_img2vid`

Content-Type: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | file | Yes | Reference image |
| `prompt` | string | Yes | Animation description |
| `fps` / `frames` | int | No | Video parameters |
| + common params | | | |

---

### 8. Text + Image + Video → Video
**POST** `/api/generate/txt_img_vid2vid`

Content-Type: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | file | Yes | Style reference image |
| `video` | file | Yes | Source video to transform |
| `prompt` | string | Yes | Style/content guidance |
| `strength` | float | No (0.7) | Transform strength |
| + common params | | | |

---

## ComfyUI Proxy Endpoints

These transparently proxy to the ComfyUI backend(s). Upstream code needs no changes.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload/image` | POST | Broadcast upload to all healthy nodes |
| `/prompt` | POST | Submit workflow (load-balanced) |
| `/prompt` | GET | Aggregated queue from all nodes |
| `/history/{prompt_id}` | GET | Task history (routes to correct node) |
| `/history` | GET | All history (merged from all nodes) |
| `/view?filename=...` | GET | Fetch output file (output-affinity routing) |
| `/system_stats` | GET | Per-node system info |
| `/interrupt` | POST | Interrupt task on correct node |
| `/queue` | POST | Queue management (delete by prompt_id) |
| `/ws?clientId=...` | WebSocket | Progress stream (multiplexed from all backends) |

---

## Node Management API

### List all nodes
**GET** `/router/nodes`

```json
[
  {
    "node_id": "gpu-0",
    "host": "localhost",
    "port": 8188,
    "health": "healthy",
    "queue_pending": 2,
    "queue_running": 1,
    "queue_depth": 3
  }
]
```

### Add node (runtime, no restart)
**POST** `/router/nodes/add?node_id=gpu-2&host=10.0.0.5&port=8188`

### Remove node
**DELETE** `/router/nodes/{node_id}`

### Drain node (graceful shutdown)
**POST** `/router/nodes/{node_id}/drain`

No new tasks will be routed to the node. Existing tasks continue to completion.

### Re-enable node
**POST** `/router/nodes/{node_id}/enable`

---

## Task Management API

### List recent tasks
**GET** `/router/tasks?limit=100`

```json
[
  {
    "prompt_id": "abc-123",
    "node_id": "gpu-0",
    "client_id": "gateway-txt2img-xyz",
    "status": "completed",
    "created_at": "2026-07-20T12:00:00Z",
    "progress": null,
    "error": null
  }
]
```

Task status values: `queued` | `running` | `completed` | `error`

### Get task detail
**GET** `/router/tasks/{prompt_id}`

---

## MCP Tools (LLM Tool Calling)

### generate_txt2img
Generate an image from a text prompt.

Input:
```json
{
  "prompt": "required - describe the image",
  "negative_prompt": "optional",
  "model": "optional - checkpoint filename",
  "width": 512, "height": 512,
  "steps": 20, "cfg": 7.0, "seed": -1
}
```

### generate_txt2vid
Generate a video from a text prompt.

Input:
```json
{
  "prompt": "required - describe the video",
  "fps": 15, "frames": 16,
  "steps": 20, "cfg": 7.0, "seed": -1
}
```

---

## Error Codes

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 400 | Bad request / validation error |
| 502 | ComfyUI backend rejected request (e.g. model not found) |
| 503 | No healthy nodes available |
| 504 | Generation timed out |

---

## Features Checklist vs comfy-router Reference

| Feature | comfy-router | Our Gateway | Notes |
|---------|-------------|-------------|-------|
| Transparent proxy | YES | YES | Full parity |
| Least-Queue-Depth LB | YES | YES | + local pending tracking |
| Broadcast upload | YES | YES | Async, returns on first success |
| Task tracking (prompt->node) | YES | YES | + per-client task index |
| WS progress forwarding | YES | YES | + binary frame forwarding |
| Health check | YES | YES | + queue depth verification |
| Node drain/enable | YES | YES | + runtime add/delete |
| Memory safety (FIFO/LRU) | YES | YES | Same limits: 5k tasks, 10k files |
| Multi-modal gen endpoints | NO | YES | 8 new endpoints added |
| Mock mode | NO | YES | Full end-to-end dev testing |
| MCP tool exposure | NO | YES | Stdio + SSE |
| Workflow builder | NO | YES | Version-agnostic template engine |
| Dynamic node add via API | NO | YES | POST /router/nodes/add |

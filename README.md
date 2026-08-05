# comfy_api

A clean self-contained ComfyUI gateway repository root.

## Repository layout

This directory is intended to be used directly as the Git repository root.
Everything needed for the gateway is kept inside this directory:
- source modules
- templates
- docs
- config
- workflow storage
- static outputs

## Runtime layers

### Standard generation flow
- `/api/generate/txt2img`
- `/api/generate/img2img`
- `/api/generate/txt_img2img`
- `/api/generate/txt2vid`
- `/api/generate/img2vid`
- `/api/generate/vid2vid`
- `/api/generate/txt_img2vid`
- `/api/generate/txt_img_vid2vid`

### Independent custom workflow flow
- `GET /api/workflows`
- `POST /api/workflows`
- `GET /api/workflows/{workflow_id}`
- `PUT /api/workflows/{workflow_id}`
- `DELETE /api/workflows/{workflow_id}`
- `GET /api/workflows/{workflow_id}/interface`
- `POST /api/workflows/{workflow_id}/run`
- `POST /api/workflows/{workflow_id}/run-with-files`

### Comfy structure utilization
- proxy endpoints: `/upload/image`, `/prompt`, `/history`, `/view`, `/system_stats`, `/interrupt`, `/queue`, `/ws`
- introspection endpoints: `/api/comfy/capabilities`, `/api/comfy/object_info`, `/api/comfy/object_summary`, `/api/comfy/system_stats`
- workflow interface inference uses Comfy `object_info`
- independent workflow outputs are normalized before return

## Intelligent discovery

The gateway supports:
- localhost 8188 detection
- local process discovery
- Docker container discovery
- automatic local ComfyUI wakeup
- automatic Docker wakeup for matching Comfy containers

## Start

Install dependencies:

```bash
pip install -r requirements.txt
```

Run directly from this directory:

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8199 --reload
```

Or:

```bash
python run.py
```

## Config

Use `config.yaml` for normal local usage.

If this repository is nested under another workspace and ComfyUI lives one level up, you can copy `config.root.yaml` to `config.yaml` or adapt paths as needed.

## Workflow storage

Independent stored workflow records live in:
- `workflows/`

Standard main-flow templates live in:
- `templates/`

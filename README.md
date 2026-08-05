# comfy_api

A self-contained ComfyUI gateway with three layers:
- standard generation endpoints
- independent workflow storage and execution
- discovered workflow catalog + visual UI

## Why `/api/workflows` was empty before

Because `/api/workflows` only lists workflows imported into the gateway's own store.
It does not automatically read ComfyUI's saved workflow files.

That means these are different things:
- ComfyUI saved workflows: for example `ComfyUI/user/default/workflows/*.json`
- gateway stored workflows: persisted under `workflows/` and visible in `/api/workflows`

Now both modes are supported:
- `/api/workflow-catalog/*` scans discovered workflow files directly
- `/api/workflows/*` manages imported independent workflows

## New visual UI

Open:
- `/ui`

The UI supports:
- scanning discovered workflow files
- importing selected workflows into the gateway store
- inferring likely text/image/video inputs
- running workflows with text values or uploaded files
- previewing normalized outputs

## API groups

### Standard generation
- `/api/generate/*`

### Independent stored workflows
- `/api/workflows`
- `/api/workflows/{workflow_id}/interface`
- `/api/workflows/{workflow_id}/run`
- `/api/workflows/{workflow_id}/run-with-files`

### Discovered workflow catalog
- `/api/workflow-catalog`
- `/api/workflow-catalog/detail`
- `/api/workflow-catalog/import`

### Comfy structure introspection
- `/api/comfy/capabilities`
- `/api/comfy/object_info`
- `/api/comfy/object_summary`
- `/api/comfy/system_stats`

## Secret handling

Do not hardcode API keys into source files.
Use environment variables inside Docker, for example:

```bash
export JOYCAPTION_API_KEY=your_key_here
export OPENAI_API_KEY=your_key_here
```

Then let your custom nodes or wrappers read from env.

## Start

```bash
python -m uvicorn comfy_api.main:app --host 0.0.0.0 --port 8199
```

Then visit:
- `/ui`
- `/api/comfy/capabilities`
- `/api/workflow-catalog`

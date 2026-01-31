"""
LucidLink Labs - S3 to LucidLink Integrator
FastAPI + HTMX Web Application
"""

import asyncio
import json
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services.state import AppState
from services.job_queue import job_queue

# Application state
state = AppState()

# Connect job queue to state
job_queue.set_state(state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    state.log("Application started")
    await job_queue.start()
    yield
    await job_queue.stop()
    state.log("Application shutdown")


app = FastAPI(
    title="LucidLink Labs",
    description="S3 to LucidLink Integrator",
    lifespan=lifespan
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")


# ============== Pages ==============

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page."""
    # Get saved AWS credentials
    aws_key, aws_secret = state.get_saved_aws_credentials()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "connected": state.is_connected,
        "current_bucket": state.current_bucket,
        "current_prefix": state.current_prefix,
        "filespaces": list(state.filespaces.keys()) if state.filespaces else [],
        "datastores": list(state.datastores.keys()) if state.datastores else [],
        "selected_filespace": state.selected_filespace,
        "selected_datastore": state.selected_datastore,
        "saved_token": state.token,
        "saved_api_host": state.api_host,
        "saved_aws_key": aws_key or "",
        "has_saved_aws_secret": bool(aws_secret),
    })


# ============== Settings API ==============

@app.post("/api/load-filespaces", response_class=HTMLResponse)
async def load_filespaces(
    request: Request,
    token: str = Form(...),
    api_host: Optional[str] = Form(None),
):
    """Load filespaces from LucidLink API."""
    # Use provided api_host or fall back to saved/default
    effective_host = api_host.strip() if api_host else state.api_host

    ll_client = LucidLinkClient(api_host=effective_host)
    result = await ll_client.list_filespaces(token, api_host=effective_host)

    if isinstance(result, str):
        # Error occurred
        state.log(f"❌ Error loading filespaces: {result}")
        return templates.TemplateResponse("partials/filespace_select.html", {
            "request": request,
            "error": result,
            "filespaces": [],
        })

    state.filespaces = {fs.get("name"): fs.get("id") for fs in result}
    state.token = token
    state.api_host = effective_host
    state.log(f"✅ Loaded {len(result)} filespaces")

    return templates.TemplateResponse("partials/filespace_select.html", {
        "request": request,
        "filespaces": list(state.filespaces.keys()),
        "selected": list(state.filespaces.keys())[0] if state.filespaces else None,
    })


@app.post("/api/load-datastores", response_class=HTMLResponse)
async def load_datastores(request: Request, filespace: str = Form(...)):
    """Load datastores for selected filespace."""
    if filespace not in state.filespaces:
        return templates.TemplateResponse("partials/datastore_select.html", {
            "request": request,
            "datastores": [],
            "error": "Invalid filespace",
        })

    ll_client = LucidLinkClient()
    filespace_id = state.filespaces[filespace]
    result = await ll_client.list_datastores(state.token, filespace_id)

    state.datastores = {ds.get("name", ds.get("id")): ds.get("id") for ds in result}
    state.selected_filespace = filespace

    return templates.TemplateResponse("partials/datastore_select.html", {
        "request": request,
        "datastores": list(state.datastores.keys()),
        "selected": list(state.datastores.keys())[0] if state.datastores else None,
    })


@app.post("/api/connect", response_class=HTMLResponse)
async def connect(
    request: Request,
    bucket: str = Form(...),
    aws_key: Optional[str] = Form(None),
    aws_secret: Optional[str] = Form(None),
    datastore: str = Form(...),
):
    """Connect to S3 and LucidLink."""
    try:
        # Use saved AWS credentials if not provided
        effective_aws_key = aws_key if aws_key else None
        effective_aws_secret = aws_secret if aws_secret else None

        if not effective_aws_key or not effective_aws_secret:
            saved_key, saved_secret = state.get_saved_aws_credentials()
            if saved_key and saved_secret:
                effective_aws_key = effective_aws_key or saved_key
                effective_aws_secret = effective_aws_secret or saved_secret

        # Initialize S3 service
        state.s3_service = S3Service(
            access_key=effective_aws_key,
            secret_key=effective_aws_secret,
        )

        # Verify bucket access
        await state.s3_service.head_bucket(bucket)
        state.current_bucket = bucket

        # Configure LucidLink client
        state.ll_client = LucidLinkClient(api_host=state.api_host)
        state.ll_client.configure(
            token=state.token,
            filespace_id=state.filespaces[state.selected_filespace],
            datastore_id=state.datastores[datastore],
            api_host=state.api_host,
        )
        state.selected_datastore = datastore
        state.is_connected = True
        state.current_prefix = ""

        # Save connection state and secrets
        state.save_connection(save_secrets=True)
        if effective_aws_key and effective_aws_secret:
            state.save_aws_credentials(effective_aws_key, effective_aws_secret)

        state.log("✅ Connected successfully")

        # Return browser view
        return templates.TemplateResponse("partials/browser.html", {
            "request": request,
            "connected": True,
            "bucket": bucket,
            "prefix": "",
            "items": await state.s3_service.list_objects(bucket, ""),
        })

    except Exception as e:
        state.log(f"❌ Connection error: {e}")
        return templates.TemplateResponse("partials/connection_error.html", {
            "request": request,
            "error": str(e),
        })


# ============== Create Datastore ==============

@app.get("/api/create-datastore-modal", response_class=HTMLResponse)
async def create_datastore_modal(request: Request):
    """Return the create datastore modal HTML."""
    return templates.TemplateResponse("partials/create_datastore_modal.html", {
        "request": request,
    })


@app.post("/api/create-datastore", response_class=HTMLResponse)
async def create_datastore(
    request: Request,
    name: str = Form(...),
    bucket: str = Form(...),
    region: str = Form("us-east-1"),
    endpoint: Optional[str] = Form(None),
    access_key: str = Form(...),
    secret_key: str = Form(...),
):
    """Create a new S3 datastore in LucidLink."""
    ll_client = LucidLinkClient()
    filespace_id = state.filespaces[state.selected_filespace]

    result = await ll_client.create_s3_datastore(
        token=state.token,
        filespace_id=filespace_id,
        name=name,
        bucket=bucket,
        region=region,
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
    )

    if result == "SUCCESS":
        state.log(f"✅ DataStore '{name}' created")
        # Reload datastores
        datastores = await ll_client.list_datastores(state.token, filespace_id)
        state.datastores = {ds.get("name", ds.get("id")): ds.get("id") for ds in datastores}

        return templates.TemplateResponse("partials/datastore_select.html", {
            "request": request,
            "datastores": list(state.datastores.keys()),
            "selected": name,
            "success": f"DataStore '{name}' created successfully",
        })
    else:
        state.log(f"❌ Error creating datastore: {result}")
        return templates.TemplateResponse("partials/create_datastore_error.html", {
            "request": request,
            "error": result,
        })


# ============== S3 Browser ==============

@app.get("/api/browse", response_class=HTMLResponse)
async def browse(request: Request, prefix: str = ""):
    """Browse S3 bucket contents."""
    if not state.is_connected:
        return templates.TemplateResponse("partials/not_connected.html", {
            "request": request,
        })

    state.current_prefix = prefix
    items = await state.s3_service.list_objects(state.current_bucket, prefix)

    return templates.TemplateResponse("partials/browser_contents.html", {
        "request": request,
        "bucket": state.current_bucket,
        "prefix": prefix,
        "items": items,
        "can_go_back": bool(prefix),
    })


@app.get("/api/browse/back", response_class=HTMLResponse)
async def browse_back(request: Request):
    """Navigate back in S3 browser."""
    if state.current_prefix:
        # Go up one level
        parts = state.current_prefix.rstrip("/").split("/")
        new_prefix = "/".join(parts[:-1])
        if new_prefix:
            new_prefix += "/"
        state.current_prefix = new_prefix

    items = await state.s3_service.list_objects(state.current_bucket, state.current_prefix)

    return templates.TemplateResponse("partials/browser_contents.html", {
        "request": request,
        "bucket": state.current_bucket,
        "prefix": state.current_prefix,
        "items": items,
        "can_go_back": bool(state.current_prefix),
    })


# ============== Import Operations ==============

@app.post("/api/import/file")
async def import_file(request: Request, key: str = Form(...)):
    """Import a single file from S3 to LucidLink."""
    state.log(f"📄 Importing: {key}")
    state.progress = 0.1

    try:
        # Build LucidLink path nested under bucket name
        bucket_name = state.current_bucket
        ll_path = f"/{bucket_name}/{key}"

        # Ensure folder structure exists (including bucket folder)
        structure_ok, structure_error = await state.ll_client.ensure_structure(ll_path)
        if structure_ok:
            state.progress = 0.6
            code, error_msg = await state.ll_client.import_file(key, ll_path)

            if code in [200, 201]:
                state.log(f"✅ Success: {key.split('/')[-1]}")
            elif code in [400, 409]:
                state.log(f"⏭️ Already exists: {key.split('/')[-1]}")
            else:
                state.log(f"❌ Error ({code}): {key.split('/')[-1]} - {error_msg}")
        else:
            state.log(f"❌ Failed to create folder structure for: {key} - {structure_error}")

        state.progress = 1.0
        return {"status": "complete"}

    except Exception as e:
        state.log(f"❌ Exception: {e}")
        state.progress = 1.0
        return {"status": "error", "message": str(e)}


@app.post("/api/import/folder", response_class=HTMLResponse)
async def import_folder(request: Request, prefix: str = Form(...)):
    """Add a folder import job to the queue."""
    if not state.is_connected:
        return templates.TemplateResponse("partials/job_error.html", {
            "request": request,
            "error": "Not connected",
        })

    # Validate we have the required IDs
    filespace_id = state.filespaces.get(state.selected_filespace, "")
    datastore_id = state.datastores.get(state.selected_datastore, "")

    if not filespace_id or not datastore_id:
        return templates.TemplateResponse("partials/job_error.html", {
            "request": request,
            "error": "Missing filespace or datastore configuration",
        })

    job_id = await job_queue.add_job(
        bucket=state.current_bucket,
        prefix=prefix,
        filespace_id=filespace_id,
        datastore_id=datastore_id,
    )

    # Return updated job queue partial
    return templates.TemplateResponse("partials/job_added.html", {
        "request": request,
        "job_id": job_id,
        "prefix": prefix,
    })


# ============== Job Queue API ==============

@app.get("/api/jobs", response_class=HTMLResponse)
async def list_jobs(request: Request):
    """Get the job queue list."""
    jobs = job_queue.get_jobs()
    status = job_queue.get_queue_status()

    return templates.TemplateResponse("partials/job_queue.html", {
        "request": request,
        "jobs": jobs,
        "queue_status": status,
    })


@app.post("/api/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_job(request: Request, job_id: int):
    """Cancel a job."""
    job_queue.cancel_job(job_id)
    jobs = job_queue.get_jobs()
    status = job_queue.get_queue_status()

    return templates.TemplateResponse("partials/job_queue.html", {
        "request": request,
        "jobs": jobs,
        "queue_status": status,
    })


@app.delete("/api/jobs/{job_id}", response_class=HTMLResponse)
async def delete_job(request: Request, job_id: int):
    """Delete a job from history."""
    from services import database as db
    db.delete_job(job_id)
    jobs = job_queue.get_jobs()
    status = job_queue.get_queue_status()

    return templates.TemplateResponse("partials/job_queue.html", {
        "request": request,
        "jobs": jobs,
        "queue_status": status,
    })


@app.post("/api/jobs/clear", response_class=HTMLResponse)
async def clear_jobs(request: Request):
    """Clear completed jobs."""
    from services import database as db
    db.clear_completed_jobs()
    jobs = job_queue.get_jobs()
    status = job_queue.get_queue_status()

    return templates.TemplateResponse("partials/job_queue.html", {
        "request": request,
        "jobs": jobs,
        "queue_status": status,
    })


# ============== Server-Sent Events ==============

@app.get("/api/logs/stream")
async def logs_stream(request: Request):
    """Stream activity logs via SSE."""
    async def event_generator():
        last_index = 0
        while True:
            if await request.is_disconnected():
                break

            # Send new log entries
            if len(state.logs) > last_index:
                for log in state.logs[last_index:]:
                    yield f"data: {json.dumps({'type': 'log', 'message': log})}\n\n"
                last_index = len(state.logs)

            await asyncio.sleep(0.1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/api/progress/stream")
async def progress_stream(request: Request):
    """Stream progress updates via SSE."""
    async def event_generator():
        last_progress = -1
        while True:
            if await request.is_disconnected():
                break

            if state.progress != last_progress:
                yield f"data: {json.dumps({'type': 'progress', 'value': state.progress})}\n\n"
                last_progress = state.progress

            await asyncio.sleep(0.05)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/api/logs/clear", response_class=HTMLResponse)
async def clear_logs():
    """Clear the activity log."""
    state.logs.clear()
    return ""


# ============== Tabs ==============

@app.get("/api/tab/settings", response_class=HTMLResponse)
async def tab_settings(request: Request):
    """Return settings tab content."""
    # Get saved AWS credentials (masked for display)
    aws_key, aws_secret = state.get_saved_aws_credentials()

    return templates.TemplateResponse("partials/settings.html", {
        "request": request,
        "filespaces": list(state.filespaces.keys()) if state.filespaces else [],
        "datastores": list(state.datastores.keys()) if state.datastores else [],
        "selected_filespace": state.selected_filespace,
        "selected_datastore": state.selected_datastore,
        "current_bucket": state.current_bucket,
        "saved_token": state.token,
        "saved_api_host": state.api_host,
        "saved_aws_key": aws_key or "",
        "has_saved_aws_secret": bool(aws_secret),
    })


@app.get("/api/tab/browser", response_class=HTMLResponse)
async def tab_browser(request: Request):
    """Return browser tab content."""
    if not state.is_connected:
        return templates.TemplateResponse("partials/not_connected.html", {
            "request": request,
        })

    items = await state.s3_service.list_objects(state.current_bucket, state.current_prefix)

    return templates.TemplateResponse("partials/browser.html", {
        "request": request,
        "connected": True,
        "bucket": state.current_bucket,
        "prefix": state.current_prefix,
        "items": items,
    })


@app.get("/api/tab/logs", response_class=HTMLResponse)
async def tab_logs(request: Request):
    """Return logs tab content."""
    return templates.TemplateResponse("partials/logs.html", {
        "request": request,
        "logs": state.logs,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

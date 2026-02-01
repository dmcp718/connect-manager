"""
LucidLink Labs: CONNECT Manager - S3 to LucidLink Integrator
FastAPI + HTMX Web Application
DataStore-centric architecture
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
from services import database as db

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
    title="LucidLink Labs: CONNECT Manager",
    description="S3 to LucidLink Integrator - DataStore-centric",
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
    browsable_datastores = state.get_browsable_datastores()

    # Build datastores data for list view
    datastores_data = []
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        datastores_data.append({
            "id": ds.get("id"),
            "name": name,
            "bucket": s3_params.get("bucketName", ""),
        })

    return templates.TemplateResponse("index.html", {
        "request": request,
        "connected": len(browsable_datastores) > 0,
        "filespaces": list(state.filespaces.keys()) if state.filespaces else [],
        "datastores": datastores_data,
        "selected_filespace": state.selected_filespace,
        "selected_datastore": state.selected_datastore,
        "saved_token": state.token,
        "saved_api_host": state.api_host,
        "browsable_datastores": browsable_datastores,
    })


# ============== Settings API ==============

@app.post("/api/load-filespaces", response_class=HTMLResponse)
async def load_filespaces(
    request: Request,
    token: str = Form(...),
    api_host: Optional[str] = Form(None),
):
    """Load filespaces from LucidLink API and auto-load datastores for first filespace."""
    # Use provided api_host or fall back to saved/default
    effective_host = api_host.strip() if api_host else state.api_host

    ll_client = LucidLinkClient(api_host=effective_host)
    result = await ll_client.list_filespaces(token, api_host=effective_host)

    if isinstance(result, str):
        # Error occurred
        state.log(f"Error loading filespaces: {result}")
        return templates.TemplateResponse("partials/filespace_select.html", {
            "request": request,
            "error": result,
            "filespaces": [],
            "datastores": [],
        })

    state.filespaces = {fs.get("name"): fs.get("id") for fs in result}
    state.token = token
    state.api_host = effective_host
    state.log(f"Loaded {len(result)} filespaces")

    # Auto-load datastores for the first filespace
    datastores_data = []
    selected_filespace = None
    if state.filespaces:
        selected_filespace = list(state.filespaces.keys())[0]
        filespace_id = state.filespaces[selected_filespace]
        ds_result = await ll_client.list_datastores(token, filespace_id, api_host=effective_host)

        state.datastores = {}
        for ds in ds_result:
            ds_id = ds.get("id")
            ds_name = ds.get("name", ds_id)
            state.datastores[ds_name] = ds
            # Extract bucket info for display
            s3_params = ds.get("s3StorageParams", {})
            datastores_data.append({
                "id": ds_id,
                "name": ds_name,
                "bucket": s3_params.get("bucketName", ""),
            })

        state.selected_filespace = selected_filespace
        state.log(f"Loaded {len(ds_result)} datastores for {selected_filespace}")

    return templates.TemplateResponse("partials/filespace_select.html", {
        "request": request,
        "filespaces": list(state.filespaces.keys()),
        "selected": selected_filespace,
        "datastores": datastores_data,
    })


@app.post("/api/load-datastores", response_class=HTMLResponse)
async def load_datastores(request: Request, filespace: str = Form(...)):
    """Load datastores for selected filespace - returns list view."""
    if filespace not in state.filespaces:
        return templates.TemplateResponse("partials/datastore_list.html", {
            "request": request,
            "datastores": [],
            "error": "Invalid filespace",
        })

    ll_client = LucidLinkClient(api_host=state.api_host)
    filespace_id = state.filespaces[filespace]
    result = await ll_client.list_datastores(state.token, filespace_id, api_host=state.api_host)

    # Store full DataStore info including bucket details
    state.datastores = {}
    datastores_data = []
    for ds in result:
        ds_id = ds.get("id")
        ds_name = ds.get("name", ds_id)
        state.datastores[ds_name] = ds  # Store full object
        # Extract bucket info for display
        s3_params = ds.get("s3StorageParams", {})
        datastores_data.append({
            "id": ds_id,
            "name": ds_name,
            "bucket": s3_params.get("bucketName", ""),
        })

    state.selected_filespace = filespace
    state.log(f"Loaded {len(result)} datastores for {filespace}")

    return templates.TemplateResponse("partials/datastore_list.html", {
        "request": request,
        "datastores": datastores_data,
    })


@app.post("/api/connect", response_class=HTMLResponse)
async def connect(
    request: Request,
    datastore: str = Form(...),
):
    """Connect to a DataStore - show credentials modal if needed."""
    try:
        # Get DataStore info
        ds_info = state.datastores.get(datastore)
        if not ds_info:
            raise ValueError(f"DataStore '{datastore}' not found")

        datastore_id = ds_info.get("id")
        filespace_id = state.filespaces.get(state.selected_filespace, "")

        if not filespace_id:
            raise ValueError("Please select a filespace first")

        # Check if we already have credentials for this DataStore
        existing_creds = db.get_datastore_credentials(datastore_id)
        if existing_creds:
            # Already have credentials - go directly to browser
            state.selected_datastore = datastore
            state.save_connection(save_secrets=True)
            state.log(f"Connected to DataStore: {datastore}")

            return templates.TemplateResponse("partials/browser.html", {
                "request": request,
                "datastores": state.get_browsable_datastores(),
                "active_datastore_id": datastore_id,
            })

        # Need credentials - show modal
        # Extract bucket info from DataStore if available
        s3_params = ds_info.get("s3StorageParams", {})
        bucket_name = s3_params.get("bucketName", "")
        region = s3_params.get("region", "")
        endpoint = s3_params.get("endpoint", "")

        return templates.TemplateResponse("partials/datastore_credentials_modal.html", {
            "request": request,
            "datastore_id": datastore_id,
            "datastore_name": datastore,
            "filespace_id": filespace_id,
            "filespace_name": state.selected_filespace,
            "bucket_name": bucket_name,
            "region": region,
            "endpoint": endpoint,
        })

    except Exception as e:
        state.log(f"Connection error: {e}")
        return templates.TemplateResponse("partials/connection_error.html", {
            "request": request,
            "error": str(e),
        })


# ============== DataStore Credentials API ==============

@app.get("/api/datastores/{datastore_id}/credentials-modal", response_class=HTMLResponse)
async def datastore_credentials_modal(request: Request, datastore_id: str):
    """Return the credentials prompt modal for a DataStore."""
    # Find the DataStore info
    ds_info = None
    ds_name = ""
    for name, info in state.datastores.items():
        if info.get("id") == datastore_id:
            ds_info = info
            ds_name = name
            break

    if not ds_info:
        return templates.TemplateResponse("partials/connection_error.html", {
            "request": request,
            "error": f"DataStore {datastore_id} not found",
        })

    s3_params = ds_info.get("s3StorageParams", {})

    return templates.TemplateResponse("partials/datastore_credentials_modal.html", {
        "request": request,
        "datastore_id": datastore_id,
        "datastore_name": ds_name,
        "filespace_id": state.filespaces.get(state.selected_filespace, ""),
        "filespace_name": state.selected_filespace,
        "bucket_name": s3_params.get("bucketName", ""),
        "region": s3_params.get("region", ""),
        "endpoint": s3_params.get("endpoint", ""),
    })


@app.post("/api/datastores/{datastore_id}/credentials", response_class=HTMLResponse)
async def save_datastore_credentials(
    request: Request,
    datastore_id: str,
    datastore_name: str = Form(...),
    filespace_id: str = Form(...),
    filespace_name: str = Form(...),
    bucket_name: str = Form(...),
    access_key: str = Form(...),
    secret_key: str = Form(...),
    region: Optional[str] = Form(None),
    endpoint: Optional[str] = Form(None),
):
    """Save credentials for a DataStore."""
    try:
        # Validate credentials by testing S3 connection
        s3_service = S3Service(
            access_key=access_key,
            secret_key=secret_key,
            region=region or "us-east-1",
            endpoint_url=endpoint if endpoint else None,
        )

        # Try to access the bucket
        await s3_service.head_bucket(bucket_name)

        # Save credentials
        state.save_datastore_for_browsing(
            datastore_id=datastore_id,
            datastore_name=datastore_name,
            filespace_id=filespace_id,
            filespace_name=filespace_name,
            bucket_name=bucket_name,
            region=region,
            endpoint=endpoint,
            aws_access_key=access_key,
            aws_secret_key=secret_key,
        )

        state.selected_datastore = datastore_name
        state.save_connection(save_secrets=True)

        # Return browser view
        return templates.TemplateResponse("partials/browser.html", {
            "request": request,
            "datastores": state.get_browsable_datastores(),
            "active_datastore_id": datastore_id,
        })

    except Exception as e:
        state.log(f"Failed to save credentials: {e}")
        return templates.TemplateResponse("partials/connection_error.html", {
            "request": request,
            "error": f"Failed to connect to S3: {e}",
        })


@app.delete("/api/datastores/{datastore_id}/credentials", response_class=HTMLResponse)
async def delete_datastore_credentials(request: Request, datastore_id: str):
    """Remove credentials for a DataStore."""
    state.remove_datastore_credentials(datastore_id)
    return await tab_settings(request)


# ============== DataStore Management API ==============

@app.get("/api/datastores/{datastore_id}/info", response_class=HTMLResponse)
async def datastore_info(request: Request, datastore_id: str):
    """Get DataStore info and show in modal."""
    # Find filespace_id for this datastore
    filespace_id = None
    ds_name = None
    for name, info in state.datastores.items():
        if info.get("id") == datastore_id:
            ds_name = name
            filespace_id = state.filespaces.get(state.selected_filespace)
            break

    if not filespace_id:
        return templates.TemplateResponse("partials/datastore_info_modal.html", {
            "request": request,
            "error": "DataStore or filespace not found",
        })

    ll_client = LucidLinkClient(api_host=state.api_host)
    result = await ll_client.get_datastore(state.token, filespace_id, datastore_id, api_host=state.api_host)

    if isinstance(result, str):
        # Error occurred
        return templates.TemplateResponse("partials/datastore_info_modal.html", {
            "request": request,
            "error": result,
        })

    # Extract display data
    s3_params = result.get("s3StorageParams", {})

    return templates.TemplateResponse("partials/datastore_info_modal.html", {
        "request": request,
        "datastore": {
            "name": result.get("name", ds_name),
            "id": result.get("id", datastore_id),
            "kind": result.get("kind", "Unknown"),
            "bucket": s3_params.get("bucketName", ""),
            "region": s3_params.get("region", ""),
            "endpoint": s3_params.get("endpoint", ""),
            "virtual_addressing": s3_params.get("useVirtualAddressing", False),
            "url_expiration": s3_params.get("urlExpirationMinutes", ""),
        },
    })


@app.delete("/api/datastores/{datastore_id}", response_class=HTMLResponse)
async def delete_datastore(request: Request, datastore_id: str):
    """Delete DataStore from LucidLink."""
    # Find filespace_id for this datastore
    filespace_id = None
    ds_name = None
    for name, info in state.datastores.items():
        if info.get("id") == datastore_id:
            ds_name = name
            filespace_id = state.filespaces.get(state.selected_filespace)
            break

    if not filespace_id:
        return templates.TemplateResponse("partials/datastore_list.html", {
            "request": request,
            "datastores": [],
            "error": "DataStore or filespace not found",
        })

    ll_client = LucidLinkClient(api_host=state.api_host)
    result = await ll_client.delete_datastore(state.token, filespace_id, datastore_id, api_host=state.api_host)

    if result != "SUCCESS":
        state.log(f"Error deleting DataStore: {result}")
        # Return current list with error
        datastores_data = []
        for name, ds in state.datastores.items():
            s3_params = ds.get("s3StorageParams", {})
            datastores_data.append({
                "id": ds.get("id"),
                "name": name,
                "bucket": s3_params.get("bucketName", ""),
            })
        return templates.TemplateResponse("partials/datastore_list.html", {
            "request": request,
            "datastores": datastores_data,
            "error": result,
        })

    state.log(f"Deleted DataStore: {ds_name}")

    # Remove from local state
    if ds_name in state.datastores:
        del state.datastores[ds_name]

    # Also remove any saved credentials for this datastore
    state.remove_datastore_credentials(datastore_id)

    # Return updated list
    datastores_data = []
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        datastores_data.append({
            "id": ds.get("id"),
            "name": name,
            "bucket": s3_params.get("bucketName", ""),
        })

    return templates.TemplateResponse("partials/datastore_list.html", {
        "request": request,
        "datastores": datastores_data,
        "success": f"DataStore '{ds_name}' deleted",
    })


# ============== Create DataStore ==============

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
    access_key: str = Form(...),
    secret_key: str = Form(...),
    region: str = Form(...),
    endpoint: Optional[str] = Form(None),
    use_virtual_addressing: Optional[str] = Form(None),
    url_expiration_minutes: Optional[int] = Form(10080),
):
    """Create a new S3 datastore in LucidLink and save credentials locally."""
    ll_client = LucidLinkClient()
    filespace_id = state.filespaces[state.selected_filespace]

    # Determine virtual addressing
    virtual_addr = use_virtual_addressing == "true" if use_virtual_addressing else True

    result = await ll_client.create_s3_datastore(
        token=state.token,
        filespace_id=filespace_id,
        name=name,
        bucket=bucket,
        region=region,
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        use_virtual_addressing=virtual_addr,
        url_expiration_minutes=url_expiration_minutes or 10080,
    )

    if result == "SUCCESS":
        state.log(f"DataStore '{name}' created")

        # Reload datastores to get the new one's ID
        datastores = await ll_client.list_datastores(state.token, filespace_id)
        state.datastores = {}
        datastores_data = []
        new_datastore_id = None

        for ds in datastores:
            ds_id = ds.get("id")
            ds_name = ds.get("name", ds_id)
            state.datastores[ds_name] = ds
            s3_params = ds.get("s3StorageParams", {})
            datastores_data.append({
                "id": ds_id,
                "name": ds_name,
                "bucket": s3_params.get("bucketName", ""),
            })
            if ds_name == name:
                new_datastore_id = ds_id

        # Auto-save credentials for the new DataStore
        if new_datastore_id:
            state.save_datastore_for_browsing(
                datastore_id=new_datastore_id,
                datastore_name=name,
                filespace_id=filespace_id,
                filespace_name=state.selected_filespace,
                bucket_name=bucket,
                region=region,
                endpoint=endpoint,
                aws_access_key=access_key,
                aws_secret_key=secret_key,
            )

        return templates.TemplateResponse("partials/datastore_list.html", {
            "request": request,
            "datastores": datastores_data,
            "success": f"DataStore '{name}' created successfully",
        })
    else:
        state.log(f"Error creating datastore: {result}")
        return templates.TemplateResponse("partials/create_datastore_error.html", {
            "request": request,
            "error": result,
        })


# ============== S3 Browser ==============

@app.get("/api/browse/{datastore_id}", response_class=HTMLResponse)
async def browse_datastore(
    request: Request,
    datastore_id: str,
    prefix: str = "",
):
    """Browse S3 for a specific DataStore."""
    cred = state.get_datastore_by_id(datastore_id)
    if not cred:
        return templates.TemplateResponse("partials/connection_error.html", {
            "request": request,
            "error": f"DataStore credentials not found. Please add credentials in Settings.",
        })

    s3_service = state.get_s3_service_for_datastore(datastore_id)
    if not s3_service:
        return templates.TemplateResponse("partials/connection_error.html", {
            "request": request,
            "error": "S3 service not initialized for this DataStore",
        })

    try:
        bucket = cred.get("bucket_name")
        items = await s3_service.list_objects(bucket, prefix)

        return templates.TemplateResponse("partials/datastore_browser.html", {
            "request": request,
            "datastore_id": datastore_id,
            "bucket": bucket,
            "prefix": prefix,
            "items": items,
        })

    except Exception as e:
        state.log(f"Browse error: {e}")
        return templates.TemplateResponse("partials/connection_error.html", {
            "request": request,
            "error": str(e),
        })


@app.get("/api/browse/{datastore_id}/back", response_class=HTMLResponse)
async def browse_datastore_back(
    request: Request,
    datastore_id: str,
    prefix: str = "",
):
    """Navigate back in DataStore S3 browser."""
    # Calculate new prefix (go up one level)
    new_prefix = ""
    if prefix:
        parts = prefix.rstrip("/").split("/")
        new_prefix = "/".join(parts[:-1])
        if new_prefix:
            new_prefix += "/"

    return await browse_datastore(request, datastore_id, new_prefix)


# ============== Import Operations ==============

@app.post("/api/import/file")
async def import_file(
    request: Request,
    key: str = Form(...),
    datastore_id: str = Form(...),
):
    """Import a single file from S3 to LucidLink."""
    state.log(f"Importing: {key}")
    state.progress = 0.1

    try:
        # Get DataStore credentials
        cred = state.get_datastore_by_id(datastore_id)
        if not cred:
            raise ValueError(f"DataStore credentials not found")

        bucket_name = cred.get("bucket_name", "")
        filespace_id = cred.get("filespace_id", "")

        if not bucket_name:
            raise ValueError("No bucket specified")

        # Create LucidLink client for this import
        ll_client = LucidLinkClient(api_host=state.api_host)
        ll_client.configure(
            token=state.token,
            filespace_id=filespace_id,
            datastore_id=datastore_id,
            api_host=state.api_host,
        )

        # Build LucidLink path nested under bucket name
        ll_path = f"/{bucket_name}/{key}"

        # Ensure folder structure exists
        structure_ok, structure_error = await ll_client.ensure_structure(ll_path)
        if structure_ok:
            state.progress = 0.6
            code, error_msg = await ll_client.import_file(key, ll_path)

            if code in [200, 201]:
                state.log(f"Success: {key.split('/')[-1]}")
            elif code in [400, 409]:
                state.log(f"Already exists: {key.split('/')[-1]}")
            else:
                state.log(f"Error ({code}): {key.split('/')[-1]} - {error_msg}")
        else:
            state.log(f"Failed to create folder structure for: {key} - {structure_error}")

        await ll_client.close()
        state.progress = 1.0
        return {"status": "complete"}

    except Exception as e:
        state.log(f"Exception: {e}")
        state.progress = 1.0
        return {"status": "error", "message": str(e)}


@app.post("/api/import/folder", response_class=HTMLResponse)
async def import_folder(
    request: Request,
    prefix: str = Form(...),
    datastore_id: str = Form(...),
):
    """Add a folder import job to the queue."""
    # Get DataStore credentials
    cred = state.get_datastore_by_id(datastore_id)
    if not cred:
        return templates.TemplateResponse("partials/job_error.html", {
            "request": request,
            "error": f"DataStore credentials not found",
        })

    filespace_id = cred.get("filespace_id", "")
    bucket = cred.get("bucket_name", "")

    if not filespace_id or not datastore_id:
        return templates.TemplateResponse("partials/job_error.html", {
            "request": request,
            "error": "Missing filespace or datastore configuration",
        })

    if not bucket:
        return templates.TemplateResponse("partials/job_error.html", {
            "request": request,
            "error": "No bucket specified",
        })

    job_id = await job_queue.add_job(
        bucket=bucket,
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
    browsable_datastores = state.get_browsable_datastores()

    # Build datastores data for list view
    datastores_data = []
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        datastores_data.append({
            "id": ds.get("id"),
            "name": name,
            "bucket": s3_params.get("bucketName", ""),
        })

    return templates.TemplateResponse("partials/settings.html", {
        "request": request,
        "filespaces": list(state.filespaces.keys()) if state.filespaces else [],
        "datastores": datastores_data,
        "selected_filespace": state.selected_filespace,
        "selected_datastore": state.selected_datastore,
        "saved_token": state.token,
        "saved_api_host": state.api_host,
        "browsable_datastores": browsable_datastores,
    })


@app.get("/api/tab/browser", response_class=HTMLResponse)
async def tab_browser(request: Request):
    """Return browser tab content."""
    browsable_datastores = state.get_browsable_datastores()

    if browsable_datastores:
        return templates.TemplateResponse("partials/browser.html", {
            "request": request,
            "datastores": browsable_datastores,
            "active_datastore_id": browsable_datastores[0]["datastore_id"] if browsable_datastores else None,
        })

    return templates.TemplateResponse("partials/not_connected.html", {
        "request": request,
    })


@app.get("/api/tab/logs", response_class=HTMLResponse)
async def tab_logs(request: Request):
    """Return logs tab content."""
    return templates.TemplateResponse("partials/logs.html", {
        "request": request,
        "logs": state.logs,
    })


@app.get("/api/tab/help", response_class=HTMLResponse)
async def tab_help(request: Request):
    """Return help tab content."""
    return templates.TemplateResponse("partials/help.html", {
        "request": request,
        "api_host": state.api_host,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

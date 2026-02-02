"""
LucidLink Labs | CONNECT Manager - S3 to LucidLink Integrator
FastAPI + HTMX Web Application
DataStore-centric architecture with multi-user support
"""

import asyncio
import json
import os
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.lucidlink import LucidLinkClient
from services.s3_service import S3Service
from services.user_state import UserSession, user_state_manager, get_user_session
from services.job_queue import job_queue
from services import database as db
from services import auth as auth_service
from routes.auth import router as auth_router, get_current_user, get_current_user_optional
from middleware.auth import AuthMiddleware
from models.user import TokenData


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Initialize database tables
    db.init_db()
    # Ensure at least one admin user exists
    auth_service.ensure_admin_exists()
    await job_queue.start()
    yield
    await job_queue.stop()


app = FastAPI(
    title="LucidLink Labs | CONNECT Manager",
    description="S3 to LucidLink Integrator - Multi-user",
    lifespan=lifespan
)

# Add authentication middleware
app.add_middleware(AuthMiddleware)

# Include auth routes
app.include_router(auth_router)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")


# Helper to get user session from request
def get_session_from_request(request: Request) -> UserSession:
    """Get user session from request state."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return get_user_session(user_id)


# ============== Health Check ==============

@app.get("/health")
async def health_check():
    """Health check endpoint (no auth required)."""
    return {"status": "healthy"}


# ============== Pages ==============

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    # Check if already logged in
    user = get_current_user_optional(request)
    if user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)

    # Only show default credentials hint if using defaults (dev mode)
    show_default_hint = (
        os.getenv("ADMIN_EMAIL", "admin@localhost") == "admin@localhost" and
        os.getenv("ADMIN_PASSWORD", "admin") == "admin"
    )

    return templates.TemplateResponse("login.html", {
        "request": request,
        "show_default_hint": show_default_hint,
    })


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main page."""
    state = get_session_from_request(request)
    browsable_datastores = state.get_browsable_datastores()
    user_id = getattr(request.state, "user_id", None)

    # Build datastores data for list view
    datastores_data = []
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        ds_id = ds.get("id")
        has_creds = db.get_datastore_credentials(ds_id, user_id=user_id) is not None
        datastores_data.append({
            "id": ds_id,
            "name": name,
            "bucket": s3_params.get("bucketName", ""),
            "has_credentials": has_creds,
        })

    # Get user info for template
    user_email = getattr(request.state, "user_email", "")
    is_admin = getattr(request.state, "is_admin", False)

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
        "user_email": user_email,
        "is_admin": is_admin,
    })


# ============== Settings API ==============

@app.post("/api/load-filespaces", response_class=HTMLResponse)
async def load_filespaces(
    request: Request,
    token: str = Form(...),
    api_host: Optional[str] = Form(None),
):
    """Load filespaces from LucidLink API and auto-load datastores for first filespace."""
    state = get_session_from_request(request)

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
    state.save_connection(save_secrets=True)  # Persist token and API host
    state.log(f"Loaded {len(result)} filespaces")

    # Auto-load datastores for the first filespace
    datastores_data = []
    selected_filespace = None
    user_id = getattr(request.state, "user_id", None)
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
            # Check if user has credentials for this datastore
            has_creds = db.get_datastore_credentials(ds_id, user_id=user_id) is not None
            datastores_data.append({
                "id": ds_id,
                "name": ds_name,
                "bucket": s3_params.get("bucketName", ""),
                "has_credentials": has_creds,
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
    state = get_session_from_request(request)
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
    user_id = getattr(request.state, "user_id", None)
    for ds in result:
        ds_id = ds.get("id")
        ds_name = ds.get("name", ds_id)
        state.datastores[ds_name] = ds  # Store full object
        # Extract bucket info for display
        s3_params = ds.get("s3StorageParams", {})
        # Check if user has credentials for this datastore
        has_creds = db.get_datastore_credentials(ds_id, user_id=user_id) is not None
        datastores_data.append({
            "id": ds_id,
            "name": ds_name,
            "bucket": s3_params.get("bucketName", ""),
            "has_credentials": has_creds,
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
    state = get_session_from_request(request)
    try:
        # Get DataStore info
        ds_info = state.datastores.get(datastore)
        if not ds_info:
            raise ValueError(f"DataStore '{datastore}' not found")

        datastore_id = ds_info.get("id")
        filespace_id = state.filespaces.get(state.selected_filespace, "")

        if not filespace_id:
            raise ValueError("Please select a filespace first")

        # Check if we already have credentials for this DataStore (user-specific)
        user_id = getattr(request.state, "user_id", None)
        existing_creds = db.get_datastore_credentials(datastore_id, user_id=user_id)
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
    state = get_session_from_request(request)
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
    state = get_session_from_request(request)
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
    state = get_session_from_request(request)
    state.remove_datastore_credentials(datastore_id)
    return await tab_settings(request)


# ============== DataStore Management API ==============

@app.get("/api/datastores/{datastore_id}/info", response_class=HTMLResponse)
async def datastore_info(request: Request, datastore_id: str):
    """Get DataStore info and show in modal."""
    state = get_session_from_request(request)
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
    state = get_session_from_request(request)
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
        user_id = getattr(request.state, "user_id", None)
        for name, ds in state.datastores.items():
            s3_params = ds.get("s3StorageParams", {})
            ds_id = ds.get("id")
            has_creds = db.get_datastore_credentials(ds_id, user_id=user_id) is not None
            datastores_data.append({
                "id": ds_id,
                "name": name,
                "bucket": s3_params.get("bucketName", ""),
                "has_credentials": has_creds,
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
    user_id = getattr(request.state, "user_id", None)
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        ds_id = ds.get("id")
        has_creds = db.get_datastore_credentials(ds_id, user_id=user_id) is not None
        datastores_data.append({
            "id": ds_id,
            "name": name,
            "bucket": s3_params.get("bucketName", ""),
            "has_credentials": has_creds,
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
    _ = get_session_from_request(request)  # Verify authenticated
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
    state = get_session_from_request(request)
    # Validate S3 access before creating DataStore
    try:
        test_s3 = S3Service(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            endpoint_url=endpoint if endpoint else None,
        )
        # Test bucket access
        await test_s3.head_bucket(bucket)
        state.log(f"S3 access validated for bucket '{bucket}'")
    except ValueError as e:
        # head_bucket raises ValueError for access/not found errors
        state.log(f"S3 validation failed: {e}")
        return templates.TemplateResponse("partials/create_datastore_error.html", {
            "request": request,
            "error": f"S3 access failed: {e}",
        })
    except Exception as e:
        state.log(f"S3 validation error: {e}")
        return templates.TemplateResponse("partials/create_datastore_error.html", {
            "request": request,
            "error": f"Failed to validate S3 access: {e}",
        })

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
        user_id = getattr(request.state, "user_id", None)

        for ds in datastores:
            ds_id = ds.get("id")
            ds_name = ds.get("name", ds_id)
            state.datastores[ds_name] = ds
            s3_params = ds.get("s3StorageParams", {})
            # For newly created datastore, credentials will be saved below
            # For others, check existing credentials
            has_creds = (ds_name == name) or (db.get_datastore_credentials(ds_id, user_id=user_id) is not None)
            datastores_data.append({
                "id": ds_id,
                "name": ds_name,
                "bucket": s3_params.get("bucketName", ""),
                "has_credentials": has_creds,
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

        # Return success with out-of-band swap to close modal and update list
        return templates.TemplateResponse("partials/datastore_create_success.html", {
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
    state = get_session_from_request(request)
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
    _ = get_session_from_request(request)  # Verify authenticated
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
    state = get_session_from_request(request)
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
    state = get_session_from_request(request)
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
    _ = get_session_from_request(request)  # Verify authenticated
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
    _ = get_session_from_request(request)  # Verify authenticated
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
    _ = get_session_from_request(request)  # Verify authenticated
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
    _ = get_session_from_request(request)  # Verify authenticated
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
    """Stream activity logs via SSE (per-user)."""
    state = get_session_from_request(request)

    async def event_generator():
        last_index = 0
        while True:
            if await request.is_disconnected():
                break

            # Send new log entries (per-user)
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
    """Stream progress updates via SSE (per-user)."""
    state = get_session_from_request(request)

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
async def clear_logs(request: Request):
    """Clear the activity log (per-user)."""
    state = get_session_from_request(request)
    state.logs.clear()
    return ""


# ============== Tabs ==============

@app.get("/api/tab/settings", response_class=HTMLResponse)
async def tab_settings(request: Request):
    """Return settings tab content."""
    state = get_session_from_request(request)
    browsable_datastores = state.get_browsable_datastores()
    user_id = getattr(request.state, "user_id", None)

    # Build datastores data for list view
    datastores_data = []
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        ds_id = ds.get("id")
        # Check if user has credentials for this datastore
        has_creds = db.get_datastore_credentials(ds_id, user_id=user_id) is not None
        datastores_data.append({
            "id": ds_id,
            "name": name,
            "bucket": s3_params.get("bucketName", ""),
            "has_credentials": has_creds,
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
    state = get_session_from_request(request)
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
    state = get_session_from_request(request)
    return templates.TemplateResponse("partials/logs.html", {
        "request": request,
        "logs": state.logs,
    })


@app.get("/api/tab/help", response_class=HTMLResponse)
async def tab_help(request: Request):
    """Return help tab content."""
    state = get_session_from_request(request)
    return templates.TemplateResponse("partials/help.html", {
        "request": request,
        "api_host": state.api_host,
    })


@app.get("/api/tab/account", response_class=HTMLResponse)
async def tab_account(request: Request):
    """Return account settings tab content."""
    user_id = getattr(request.state, "user_id", None)
    is_admin = getattr(request.state, "is_admin", False)

    # Get current user info
    current_user = db.get_user_by_id(user_id) if user_id else None

    # Get all users for admin view
    users = db.list_users() if is_admin else []

    return templates.TemplateResponse("partials/account_tab.html", {
        "request": request,
        "current_user": current_user,
        "users": users,
        "is_admin": is_admin,
    })


# ============== SQS Event Stream API ==============

@app.get("/api/tab/sqs", response_class=HTMLResponse)
async def tab_sqs(request: Request):
    """Return SQS tab content."""
    state = get_session_from_request(request)
    from services import secrets as sec

    sqs_credentials = db.get_sqs_credentials()
    sqs_queues = db.list_sqs_queues()
    sqs_events = db.list_sqs_events(limit=20)
    browsable_datastores = state.get_browsable_datastores()

    # Get event count for each queue and add datastore names
    for queue in sqs_queues:
        queue["events_today"] = db.get_sqs_event_count_today(queue["id"])
        # Look up datastore name
        ds_cred = db.get_datastore_credentials(queue["datastore_id"])
        if ds_cred:
            queue["datastore_name"] = ds_cred.get("datastore_name", "")
            queue["filespace_name"] = ds_cred.get("filespace_name", "")

    return templates.TemplateResponse("partials/sqs_tab.html", {
        "request": request,
        "sqs_credentials": sqs_credentials,
        "sqs_queues": sqs_queues,
        "sqs_events": sqs_events,
        "browsable_datastores": browsable_datastores,
    })


@app.post("/api/sqs/credentials", response_class=HTMLResponse)
async def save_sqs_credentials(
    request: Request,
    access_key: str = Form(...),
    secret_key: Optional[str] = Form(None),
):
    """Save SQS IAM credentials."""
    state = get_session_from_request(request)
    from services import secrets as sec
    import uuid

    try:
        # Get existing credentials if updating
        existing = db.get_sqs_credentials()

        # If no new secret key provided, keep the existing one
        if not secret_key and existing:
            secret_key_ref = existing.get("secret_key_encrypted")
        else:
            if not secret_key:
                raise ValueError("Secret key is required")
            # Generate a unique reference key and store the actual secret
            secret_key_ref = uuid.uuid4().hex[:12]
            sec.set_secret(f"sqs_secret_{secret_key_ref}", secret_key)

        # Save to database (region defaults to us-east-1, actual region determined per-queue)
        region = "us-east-1"
        db.save_sqs_credentials(access_key, secret_key_ref, region)
        state.log("SQS credentials saved")

        # Return the queue list section
        sqs_queues = db.list_sqs_queues()
        browsable_datastores = state.get_browsable_datastores()

        for queue in sqs_queues:
            queue["events_today"] = db.get_sqs_event_count_today(queue["id"])
            ds_cred = db.get_datastore_credentials(queue["datastore_id"])
            if ds_cred:
                queue["datastore_name"] = ds_cred.get("datastore_name", "")

        return templates.TemplateResponse("partials/sqs_queue_list.html", {
            "request": request,
            "sqs_credentials": db.get_sqs_credentials(),
            "sqs_queues": sqs_queues,
            "browsable_datastores": browsable_datastores,
        })

    except Exception as e:
        state.log(f"Failed to save SQS credentials: {e}")
        return templates.TemplateResponse("partials/sqs_queue_list.html", {
            "request": request,
            "sqs_credentials": db.get_sqs_credentials(),
            "sqs_queues": [],
            "browsable_datastores": state.get_browsable_datastores(),
            "error": str(e),
        })


@app.delete("/api/sqs/credentials", response_class=HTMLResponse)
async def delete_sqs_credentials(request: Request):
    """Delete SQS credentials."""
    state = get_session_from_request(request)
    from services import secrets as sec

    # Get existing to clean up the secret
    existing = db.get_sqs_credentials()
    if existing:
        secret_key_ref = existing.get("secret_key_encrypted")
        if secret_key_ref:
            sec.delete_secret(f"sqs_secret_{secret_key_ref}")

    db.delete_sqs_credentials()
    state.log("SQS credentials removed")

    return await tab_sqs(request)


@app.get("/api/sqs/queues/add-modal", response_class=HTMLResponse)
async def sqs_add_queue_modal(request: Request):
    """Show modal for adding a new SQS queue."""
    state = get_session_from_request(request)
    browsable_datastores = state.get_browsable_datastores()

    return templates.TemplateResponse("partials/sqs_queue_modal.html", {
        "request": request,
        "browsable_datastores": browsable_datastores,
    })


@app.get("/api/sqs/queues/discover", response_class=HTMLResponse)
async def discover_sqs_queues(request: Request, queue_region: str = "us-east-1"):
    """Discover existing SQS queues in a region. Returns HTML options for select."""
    from services import secrets as sec
    from services.sqs_service import SQSService, SQSError

    try:
        # Get SQS credentials
        sqs_creds = db.get_sqs_credentials()
        if not sqs_creds:
            return HTMLResponse('<option value="">No credentials configured</option>')

        access_key = sqs_creds.get("access_key")
        secret_key_ref = sqs_creds.get("secret_key_encrypted")
        secret_key = sec.get_secret(f"sqs_secret_{secret_key_ref}")

        if not access_key or not secret_key:
            return HTMLResponse('<option value="">Invalid credentials</option>')

        # List queues in the selected region
        sqs = SQSService(access_key, secret_key, queue_region)
        queues = sqs.list_queues()

        if not queues:
            return HTMLResponse(f'<option value="">No queues found in {queue_region}</option>')

        # Build HTML options
        options = ['<option value="">Select a queue...</option>']
        for q in queues:
            options.append(f'<option value="{q["url"]}">{q["name"]}</option>')

        return HTMLResponse('\n'.join(options))

    except SQSError as e:
        return HTMLResponse(f'<option value="">Error: {str(e)}</option>')
    except Exception as e:
        return HTMLResponse(f'<option value="">Error discovering queues</option>')


@app.post("/api/sqs/queues", response_class=HTMLResponse)
async def add_sqs_queue(
    request: Request,
    mode: str = Form("create"),
    queue_name: Optional[str] = Form(None),
    queue_url: Optional[str] = Form(None),
    queue_region: str = Form("us-east-1"),
    datastore_id: str = Form(...),
    import_prefix: str = Form(""),
    configure_s3_policy: Optional[str] = Form(None),
):
    """Add or create a new SQS queue configuration."""
    state = get_session_from_request(request)
    from services import secrets as sec
    from services.sqs_service import SQSService, SQSError
    import uuid

    browsable_datastores = state.get_browsable_datastores()

    try:
        # Get SQS credentials
        sqs_creds = db.get_sqs_credentials()
        if not sqs_creds:
            raise ValueError("SQS credentials not configured")

        access_key = sqs_creds.get("access_key")
        secret_key_ref = sqs_creds.get("secret_key_encrypted")
        secret_key = sec.get_secret(f"sqs_secret_{secret_key_ref}")

        if not access_key or not secret_key:
            raise ValueError("Invalid SQS credentials")

        # Get DataStore credentials to validate and get bucket name
        ds_cred = db.get_datastore_credentials(datastore_id)
        if not ds_cred:
            raise ValueError("DataStore credentials not found. Please add credentials in Settings first.")

        filespace_id = ds_cred.get("filespace_id", "")
        bucket_name = ds_cred.get("bucket_name", "")

        if mode == "create":
            # Use selected region for new queue
            region = queue_region

            # Initialize SQS service
            sqs = SQSService(access_key, secret_key, region)

            # Create a new queue
            if not queue_name:
                raise ValueError("Queue name is required")

            # Validate queue name
            import re
            if not re.match(r'^[a-zA-Z0-9_-]+$', queue_name):
                raise ValueError("Queue name can only contain alphanumeric characters, hyphens, and underscores")

            queue_info = sqs.create_queue(queue_name)
            state.log(f"Created SQS queue '{queue_name}' in AWS ({region})")

            # Configure queue policy and S3 bucket notifications
            if configure_s3_policy == "true" and bucket_name:
                # 1. Set queue policy to allow S3 to send messages
                sqs.configure_queue_for_s3(queue_info["url"], bucket_name)
                state.log(f"Configured queue policy for S3 bucket '{bucket_name}'")

                # 2. Configure S3 bucket to send notifications to the queue
                from services.sqs_service import S3NotificationService, S3NotificationError
                s3_notif = S3NotificationService(access_key, secret_key, region)
                try:
                    s3_notif.add_sqs_notification(
                        bucket_name=bucket_name,
                        queue_arn=queue_info["arn"],
                        notification_id=f"LucidLink-{queue_name}",
                    )
                    state.log(f"Configured S3 bucket '{bucket_name}' to send events to queue")
                except S3NotificationError as e:
                    # Queue created but S3 notification failed - still save the queue
                    state.log(f"Warning: Could not configure S3 notifications: {e}")

        else:
            # Use existing queue
            if not queue_url:
                raise ValueError("Queue URL or ARN is required")

            # Normalize URL input (could be ARN or URL)
            normalized_url = SQSService.normalize_queue_input(queue_url)
            if not normalized_url:
                raise ValueError("Invalid queue URL or ARN format")

            # Extract region from queue URL (format: https://sqs.{region}.amazonaws.com/...)
            # This handles both browse (where queue_region is set) and paste (where we need to extract)
            import re
            region_match = re.search(r'sqs\.([a-z0-9-]+)\.amazonaws\.com', normalized_url)
            if region_match:
                region = region_match.group(1)
            else:
                region = queue_region  # Use form value as fallback

            # Initialize SQS service with the correct region
            sqs = SQSService(access_key, secret_key, region)

            # Validate queue and get info
            queue_info = sqs.validate_queue_url(normalized_url)

        # Create queue record
        queue_id = uuid.uuid4().hex[:12]
        db.create_sqs_queue(
            queue_id=queue_id,
            queue_url=queue_info["url"],
            queue_arn=queue_info["arn"],
            name=queue_info["name"],
            region=queue_info["region"],
            datastore_id=datastore_id,
            filespace_id=filespace_id,
            import_prefix=import_prefix.strip(),
            user_id=state.user_id,
        )

        action = "created" if mode == "create" else "added"
        state.log(f"SQS queue '{queue_info['name']}' {action} for automatic imports")

        # Return updated queue list
        sqs_queues = db.list_sqs_queues()
        for queue in sqs_queues:
            queue["events_today"] = db.get_sqs_event_count_today(queue["id"])
            cred = db.get_datastore_credentials(queue["datastore_id"])
            if cred:
                queue["datastore_name"] = cred.get("datastore_name", "")

        return templates.TemplateResponse("partials/sqs_queue_list.html", {
            "request": request,
            "sqs_credentials": sqs_creds,
            "sqs_queues": sqs_queues,
            "browsable_datastores": browsable_datastores,
            "success": f"Queue '{queue_info['name']}' {action} successfully",
        })

    except SQSError as e:
        return templates.TemplateResponse("partials/sqs_queue_modal.html", {
            "request": request,
            "browsable_datastores": browsable_datastores,
            "error": f"SQS error: {e}",
        })
    except Exception as e:
        return templates.TemplateResponse("partials/sqs_queue_modal.html", {
            "request": request,
            "browsable_datastores": browsable_datastores,
            "error": str(e),
        })


@app.delete("/api/sqs/queues/{queue_id}", response_class=HTMLResponse)
async def delete_sqs_queue(request: Request, queue_id: str):
    """Delete an SQS queue configuration and clean up AWS resources."""
    state = get_session_from_request(request)
    from services import secrets as sec
    from services.sqs_service import SQSService, S3NotificationService, SQSError, S3NotificationError

    queue = db.get_sqs_queue(queue_id)
    if not queue:
        # Queue not found, just return the list
        sqs_queues = db.list_sqs_queues()
        browsable_datastores = state.get_browsable_datastores()
        for q in sqs_queues:
            q["events_today"] = db.get_sqs_event_count_today(q["id"])
            cred = db.get_datastore_credentials(q["datastore_id"])
            if cred:
                q["datastore_name"] = cred.get("datastore_name", "")
        return templates.TemplateResponse("partials/sqs_queue_list.html", {
            "request": request,
            "sqs_credentials": db.get_sqs_credentials(),
            "sqs_queues": sqs_queues,
            "browsable_datastores": browsable_datastores,
            "error": "Queue not found",
        })

    queue_name = queue.get("name", queue_id)
    queue_url = queue.get("queue_url")
    queue_arn = queue.get("queue_arn")
    queue_region = queue.get("region", "us-east-1")
    datastore_id = queue.get("datastore_id")

    # Get credentials for AWS cleanup
    sqs_creds = db.get_sqs_credentials()
    cleanup_errors = []

    if sqs_creds and queue_url:
        access_key = sqs_creds.get("access_key")
        secret_key_ref = sqs_creds.get("secret_key_encrypted")
        secret_key = sec.get_secret(f"sqs_secret_{secret_key_ref}")

        if access_key and secret_key:
            # 1. Remove S3 bucket notification
            if queue_arn and datastore_id:
                ds_cred = db.get_datastore_credentials(datastore_id)
                if ds_cred:
                    bucket_name = ds_cred.get("bucket_name")
                    if bucket_name:
                        try:
                            s3_notif = S3NotificationService(access_key, secret_key, queue_region)
                            s3_notif.remove_sqs_notification(bucket_name, queue_arn)
                            state.log(f"Removed S3 notification from bucket '{bucket_name}'")
                        except S3NotificationError as e:
                            cleanup_errors.append(f"S3 notification: {e}")

            # 2. Delete SQS queue from AWS
            try:
                sqs = SQSService(access_key, secret_key, queue_region)
                sqs.delete_queue(queue_url)
                state.log(f"Deleted SQS queue '{queue_name}' from AWS")
            except SQSError as e:
                cleanup_errors.append(f"SQS queue: {e}")

    # Delete from local database
    db.delete_sqs_queue(queue_id)

    if cleanup_errors:
        state.log(f"Queue '{queue_name}' removed (some AWS cleanup failed: {'; '.join(cleanup_errors)})")
    else:
        state.log(f"Queue '{queue_name}' fully deleted")

    # Return updated queue list
    sqs_queues = db.list_sqs_queues()
    browsable_datastores = state.get_browsable_datastores()

    for q in sqs_queues:
        q["events_today"] = db.get_sqs_event_count_today(q["id"])
        cred = db.get_datastore_credentials(q["datastore_id"])
        if cred:
            q["datastore_name"] = cred.get("datastore_name", "")

    return templates.TemplateResponse("partials/sqs_queue_list.html", {
        "request": request,
        "sqs_credentials": db.get_sqs_credentials(),
        "sqs_queues": sqs_queues,
        "browsable_datastores": browsable_datastores,
        "success": f"Queue '{queue_name}' deleted",
    })


@app.post("/api/sqs/queues/{queue_id}/pause", response_class=HTMLResponse)
async def pause_sqs_queue(request: Request, queue_id: str):
    """Pause polling for a queue."""
    state = get_session_from_request(request)
    db.update_sqs_queue(queue_id, status="paused")

    queue = db.get_sqs_queue(queue_id)
    queue_name = queue.get("name", queue_id) if queue else queue_id
    state.log(f"SQS queue '{queue_name}' paused")

    # Return updated queue list
    sqs_queues = db.list_sqs_queues()
    browsable_datastores = state.get_browsable_datastores()

    for q in sqs_queues:
        q["events_today"] = db.get_sqs_event_count_today(q["id"])
        cred = db.get_datastore_credentials(q["datastore_id"])
        if cred:
            q["datastore_name"] = cred.get("datastore_name", "")

    return templates.TemplateResponse("partials/sqs_queue_list.html", {
        "request": request,
        "sqs_credentials": db.get_sqs_credentials(),
        "sqs_queues": sqs_queues,
        "browsable_datastores": browsable_datastores,
    })


@app.post("/api/sqs/queues/{queue_id}/resume", response_class=HTMLResponse)
async def resume_sqs_queue(request: Request, queue_id: str):
    """Resume polling for a queue."""
    state = get_session_from_request(request)
    db.update_sqs_queue(queue_id, status="active", error_message="")

    queue = db.get_sqs_queue(queue_id)
    queue_name = queue.get("name", queue_id) if queue else queue_id
    state.log(f"SQS queue '{queue_name}' resumed")

    # Return updated queue list
    sqs_queues = db.list_sqs_queues()
    browsable_datastores = state.get_browsable_datastores()

    for q in sqs_queues:
        q["events_today"] = db.get_sqs_event_count_today(q["id"])
        cred = db.get_datastore_credentials(q["datastore_id"])
        if cred:
            q["datastore_name"] = cred.get("datastore_name", "")

    return templates.TemplateResponse("partials/sqs_queue_list.html", {
        "request": request,
        "sqs_credentials": db.get_sqs_credentials(),
        "sqs_queues": sqs_queues,
        "browsable_datastores": browsable_datastores,
    })


@app.get("/api/sqs/queues/{queue_id}/info", response_class=HTMLResponse)
async def sqs_queue_info(request: Request, queue_id: str):
    """Get queue details and statistics."""
    _ = get_session_from_request(request)  # Verify authenticated
    from services import secrets as sec
    from services.sqs_service import SQSService, SQSError

    queue = db.get_sqs_queue(queue_id)
    if not queue:
        return templates.TemplateResponse("partials/sqs_queue_info.html", {
            "request": request,
            "error": "Queue not found",
        })

    # Add event count
    queue["events_today"] = db.get_sqs_event_count_today(queue_id)

    # Add datastore name
    cred = db.get_datastore_credentials(queue["datastore_id"])
    if cred:
        queue["datastore_name"] = cred.get("datastore_name", "")
        queue["filespace_name"] = cred.get("filespace_name", "")

    # Try to get current queue stats from AWS
    try:
        sqs_creds = db.get_sqs_credentials()
        if sqs_creds:
            access_key = sqs_creds.get("access_key")
            secret_key_ref = sqs_creds.get("secret_key_encrypted")
            secret_key = sec.get_secret(f"sqs_secret_{secret_key_ref}")
            region = sqs_creds.get("region", "us-east-1")

            if access_key and secret_key:
                sqs = SQSService(access_key, secret_key, region)
                attrs = sqs.get_queue_attributes(queue["queue_url"])
                queue["approximate_messages"] = attrs.get("ApproximateNumberOfMessages", "0")
    except Exception:
        pass  # Ignore errors fetching queue stats

    return templates.TemplateResponse("partials/sqs_queue_info.html", {
        "request": request,
        "queue": queue,
    })


@app.get("/api/sqs/events", response_class=HTMLResponse)
async def list_sqs_events(request: Request, limit: int = 50):
    """List recent SQS events."""
    _ = get_session_from_request(request)  # Verify authenticated
    events = db.list_sqs_events(limit=limit)

    return templates.TemplateResponse("partials/sqs_events.html", {
        "request": request,
        "sqs_events": events,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

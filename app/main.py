"""
LucidLink Labs | LucidLink Connect Manager - S3 to LucidLink Integrator
FastAPI + HTMX Web Application
DataStore-centric architecture with multi-user support
"""

import asyncio
import json
import os
import uuid
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SqsCredentials, SqsEvent, SqsQueue, User
from db.repositories.datastore import (
    DatastoreCredentialsRepository,
    SqsCredentialsRepository,
    SqsEventRepository,
    SqsQueueRepository,
)
from db.repositories.job import JobRepository
from db.repositories.user import UserRepository
from services import state as state_helpers
from services.lucidlink import LucidLinkClient, LL_HOST
from services.s3_service import S3Service
from services.user_state import UserSession, get_user_session
from services.job_queue import job_queue
from services.database import get_db, shutdown_engine
from services import auth as auth_service
from services.activity_logger import ActivityLogger
from services.shutdown import graceful_lifespan
from services.metrics import start_metrics_sampler, stop_metrics_sampler
from services.database import get_engine
from routes.auth import router as auth_router, get_current_user_optional
from routes.health import router as health_router
from middleware.auth import AuthMiddleware
from middleware.metrics import PrometheusMiddleware


def _parse_uid(user_id: Optional[str]) -> Optional[uuid.UUID]:
    """Convert request.state.user_id (str|None) to UUID for repo calls."""
    return uuid.UUID(user_id) if user_id else None


def _queue_to_dict(q: SqsQueue) -> dict:
    """Serialize an SqsQueue ORM row to the dict shape sqs_queue_list.html /
    sqs_queue_info.html / sqs_tab.html expect (datetime fields → ISO strings,
    UUIDs stringified). CLAUDE.md rule #1 keeps templates untouched."""
    return {
        "id": str(q.id),
        "queue_url": q.queue_url,
        "queue_arn": q.queue_arn,
        "name": q.name,
        "region": q.region,
        "datastore_id": q.datastore_id,
        "filespace_id": q.filespace_id,
        "import_prefix": q.import_prefix,
        "status": q.status,
        "user_id": str(q.user_id) if q.user_id else None,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "last_poll_at": q.last_poll_at.isoformat() if q.last_poll_at else None,
        "error_message": q.error_message,
    }


def _event_to_dict(e: SqsEvent) -> dict:
    """Serialize an SqsEvent ORM row to the dict shape sqs_events.html
    expects. Includes `queue_name` from the eager-loaded queue relationship
    so the template can render the queue label without a follow-up query."""
    return {
        "id": str(e.id),
        "queue_id": str(e.queue_id),
        "message_id": e.message_id,
        "event_type": e.event_type,
        "bucket": e.bucket,
        "object_key": e.object_key,
        "object_size": e.object_size,
        "event_time": e.event_time.isoformat() if e.event_time else None,
        "status": e.status,
        "job_id": e.job_id,
        "error_message": e.error_message,
        "created_at": e.created_at.isoformat() if e.created_at else "",
        "queue_name": e.queue.name if e.queue is not None else None,
    }


def _creds_to_dict(c: Optional[SqsCredentials]) -> Optional[dict]:
    """Serialize an SqsCredentials ORM row for the SQS tab template.

    The template only reads `access_key` for display. We deliberately omit
    the decrypted secret from the dict — it has no template consumer and
    leaving it out keeps the value off the wire even by accident.
    """
    if c is None:
        return None
    return {
        "id": c.id,
        "access_key": c.access_key,
        "region": c.region,
        "user_id": str(c.user_id) if c.user_id else None,
    }


async def _enrich_queues_for_template(
    queues_orm: list[SqsQueue],
    session: AsyncSession,
    user_id: Optional[uuid.UUID],
) -> list[dict]:
    """Convert SqsQueue rows to template dicts and attach events_today +
    datastore_name / filespace_name. Single helper to avoid repeating the
    same loop across every SQS route handler."""
    ds_repo = DatastoreCredentialsRepository(session)
    events_repo = SqsEventRepository(session)
    out: list[dict] = []
    for q in queues_orm:
        d = _queue_to_dict(q)
        d["events_today"] = await events_repo.count_today_for_queue(q.id, user_id)
        ds_cred = await ds_repo.get_for_datastore_user(q.datastore_id, user_id)
        if ds_cred is not None:
            d["datastore_name"] = ds_cred.datastore_name or ""
            d["filespace_name"] = ds_cred.filespace_name or ""
        out.append(d)
    return out


async def _startup() -> None:
    auth_service.ensure_jwt_secret_valid()
    # Wire ActivityLogger fire-and-forget DB persistence before any route
    # (including the login endpoint) gets a chance to call ActivityLogger.log.
    from services.activity_logger import configure_persistence
    from services.database import get_sessionmaker

    sm = get_sessionmaker()
    configure_persistence(sm)
    # Bootstrap the admin user (no-op when one already exists). Needs a
    # session — done after configure_persistence so any activity log
    # emitted during bootstrap also lands in Postgres.
    async with sm() as session:
        await auth_service.ensure_admin_exists(session)
    await job_queue.start()
    # Background sampler for connect_arq_queue_depth + connect_db_pool_in_use.
    try:
        engine = get_engine()
    except Exception:
        engine = None
    start_metrics_sampler(engine=engine)


async def _shutdown() -> None:
    await stop_metrics_sampler()
    await job_queue.stop()
    await shutdown_engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler with graceful SIGTERM drain."""
    async with graceful_lifespan(app, startup=_startup, shutdown=_shutdown):
        yield


from version import __version__ as app_version  # noqa: E402

app = FastAPI(
    title="LucidLink Labs | LucidLink Connect Manager",
    description="S3 to LucidLink Integrator - Multi-user",
    version=app_version,
    lifespan=lifespan,
)

# Middleware order matters: Starlette executes added middleware in REVERSE
# order, so the LAST add_middleware wraps the OUTERMOST request. We want
# Prometheus instrumentation to wrap the entire stack (including auth) so
# 401s show up in the request counter — add it last.
app.add_middleware(AuthMiddleware)
app.add_middleware(PrometheusMiddleware)

# Include routers
app.include_router(auth_router)
app.include_router(health_router)

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
        os.getenv("ADMIN_EMAIL", "admin@localhost") == "admin@localhost"
        and os.getenv("ADMIN_PASSWORD", "admin") == "admin"
    )

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "show_default_hint": show_default_hint,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Main page."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    await state.hydrate(session, parsed_uid)
    browsable_datastores = state.get_browsable_datastores()
    ds_repo = DatastoreCredentialsRepository(session)

    # Build datastores data for list view
    datastores_data = []
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        ds_id = ds.get("id")
        has_creds = await ds_repo.get_for_datastore_user(ds_id, parsed_uid) is not None
        datastores_data.append(
            {
                "id": ds_id,
                "name": name,
                "bucket": s3_params.get("bucketName", ""),
                "has_credentials": has_creds,
            }
        )

    # Get user info for template
    user_email = getattr(request.state, "user_email", "")
    is_admin = getattr(request.state, "is_admin", False)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "connected": len(browsable_datastores) > 0,
            "filespaces": list(state.filespaces.keys()) if state.filespaces else [],
            "datastores": datastores_data,
            "selected_filespace": state.selected_filespace,
            "selected_datastore": state.selected_datastore,
            "saved_token": state.token,
            "saved_api_host": state.api_host,
            "default_api_host": LL_HOST,
            "browsable_datastores": browsable_datastores,
            "user_email": user_email,
            "is_admin": is_admin,
        },
    )


# ============== Settings API ==============


@app.post("/api/load-filespaces", response_class=HTMLResponse)
async def load_filespaces(
    request: Request,
    token: str = Form(...),
    api_host: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
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
        user_id = getattr(request.state, "user_id", None)
        # Log connection error
        ActivityLogger.app_error(f"Failed to connect: {result}", user_id=user_id)
        return templates.TemplateResponse(
            "partials/filespace_select.html",
            {
                "request": request,
                "error": result,
                "filespaces": [],
                "datastores": [],
            },
        )

    state.filespaces = {fs.get("name"): fs.get("id") for fs in result}
    state.token = token
    state.api_host = effective_host
    state.save_connection(save_secrets=True)  # token via services.secrets (sync)
    user_id_str_for_save = getattr(request.state, "user_id", None)
    await state.save_api_host(session, _parse_uid(user_id_str_for_save))
    await session.commit()
    state.log(f"Loaded {len(result)} filespaces")

    # Auto-load datastores for the first filespace
    datastores_data = []
    selected_filespace = None
    user_id = getattr(request.state, "user_id", None)
    if state.filespaces:
        selected_filespace = list(state.filespaces.keys())[0]
        filespace_id = state.filespaces[selected_filespace]
        ds_result = await ll_client.list_datastores(
            token, filespace_id, api_host=effective_host
        )

        state.datastores = {}
        ds_repo = DatastoreCredentialsRepository(session)
        parsed_uid = _parse_uid(user_id)
        for ds in ds_result:
            ds_id = ds.get("id")
            ds_name = ds.get("name", ds_id)
            state.datastores[ds_name] = ds
            # Extract bucket info for display
            s3_params = ds.get("s3StorageParams", {})
            # Check if user has credentials for this datastore
            has_creds = (
                await ds_repo.get_for_datastore_user(ds_id, parsed_uid) is not None
            )
            datastores_data.append(
                {
                    "id": ds_id,
                    "name": ds_name,
                    "bucket": s3_params.get("bucketName", ""),
                    "has_credentials": has_creds,
                }
            )

        state.selected_filespace = selected_filespace
        state.log(f"Loaded {len(ds_result)} datastores for {selected_filespace}")

        # Log successful connection to activity log
        ActivityLogger.app_connection(
            user_id,
            selected_filespace,
            len(ds_result),
        )

    return templates.TemplateResponse(
        "partials/filespace_select.html",
        {
            "request": request,
            "filespaces": list(state.filespaces.keys()),
            "selected": selected_filespace,
            "datastores": datastores_data,
        },
    )


@app.post("/api/load-datastores", response_class=HTMLResponse)
async def load_datastores(
    request: Request,
    filespace: str = Form(...),
    session: AsyncSession = Depends(get_db),
):
    """Load datastores for selected filespace - returns list view."""
    state = get_session_from_request(request)
    if filespace not in state.filespaces:
        return templates.TemplateResponse(
            "partials/datastore_list.html",
            {
                "request": request,
                "datastores": [],
                "error": "Invalid filespace",
            },
        )

    ll_client = LucidLinkClient(api_host=state.api_host)
    filespace_id = state.filespaces[filespace]
    result = await ll_client.list_datastores(
        state.token, filespace_id, api_host=state.api_host
    )

    # Store full DataStore info including bucket details
    state.datastores = {}
    datastores_data = []
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    ds_repo = DatastoreCredentialsRepository(session)
    for ds in result:
        ds_id = ds.get("id")
        ds_name = ds.get("name", ds_id)
        state.datastores[ds_name] = ds  # Store full object
        # Extract bucket info for display
        s3_params = ds.get("s3StorageParams", {})
        # Check if user has credentials for this datastore
        has_creds = await ds_repo.get_for_datastore_user(ds_id, parsed_uid) is not None
        datastores_data.append(
            {
                "id": ds_id,
                "name": ds_name,
                "bucket": s3_params.get("bucketName", ""),
                "has_credentials": has_creds,
            }
        )

    state.selected_filespace = filespace
    state.log(f"Loaded {len(result)} datastores for {filespace}")

    return templates.TemplateResponse(
        "partials/datastore_list.html",
        {
            "request": request,
            "datastores": datastores_data,
        },
    )


@app.post("/api/connect", response_class=HTMLResponse)
async def connect(
    request: Request,
    datastore: str = Form(...),
    session: AsyncSession = Depends(get_db),
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
        ds_repo = DatastoreCredentialsRepository(session)
        existing_creds = await ds_repo.get_for_datastore_user(
            datastore_id, _parse_uid(user_id)
        )
        if existing_creds:
            # Already have credentials - go directly to browser
            state.selected_datastore = datastore
            state.save_connection(save_secrets=True)
            state.log(f"Connected to DataStore: {datastore}")

            return templates.TemplateResponse(
                "partials/browser.html",
                {
                    "request": request,
                    "datastores": state.get_browsable_datastores(),
                    "active_datastore_id": datastore_id,
                },
            )

        # Need credentials - show modal
        # Extract bucket info from DataStore if available
        s3_params = ds_info.get("s3StorageParams", {})
        bucket_name = s3_params.get("bucketName", "")
        region = s3_params.get("region", "")
        endpoint = s3_params.get("endpoint", "")

        return templates.TemplateResponse(
            "partials/datastore_credentials_modal.html",
            {
                "request": request,
                "datastore_id": datastore_id,
                "datastore_name": datastore,
                "filespace_id": filespace_id,
                "filespace_name": state.selected_filespace,
                "bucket_name": bucket_name,
                "region": region,
                "endpoint": endpoint,
            },
        )

    except Exception as e:
        state.log(f"Connection error: {e}")
        return templates.TemplateResponse(
            "partials/connection_error.html",
            {
                "request": request,
                "error": str(e),
            },
        )


# ============== DataStore Credentials API ==============


@app.get(
    "/api/datastores/{datastore_id}/credentials-modal", response_class=HTMLResponse
)
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
        return templates.TemplateResponse(
            "partials/connection_error.html",
            {
                "request": request,
                "error": f"DataStore {datastore_id} not found",
            },
        )

    s3_params = ds_info.get("s3StorageParams", {})

    return templates.TemplateResponse(
        "partials/datastore_credentials_modal.html",
        {
            "request": request,
            "datastore_id": datastore_id,
            "datastore_name": ds_name,
            "filespace_id": state.filespaces.get(state.selected_filespace, ""),
            "filespace_name": state.selected_filespace,
            "bucket_name": s3_params.get("bucketName", ""),
            "region": s3_params.get("region", ""),
            "endpoint": s3_params.get("endpoint", ""),
        },
    )


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
    session: AsyncSession = Depends(get_db),
):
    """Save credentials for a DataStore."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    try:
        # Validate credentials by testing S3 connection
        s3_service = S3Service(
            access_key=access_key,
            secret_key=secret_key,
            region=region or "us-east-1",
            endpoint_url=endpoint if endpoint else None,
        )
        await s3_service.head_bucket(bucket_name)

        await state_helpers.save_datastore_credentials(
            session,
            datastore_id=datastore_id,
            datastore_name=datastore_name,
            filespace_id=filespace_id,
            filespace_name=filespace_name,
            bucket_name=bucket_name,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            endpoint=endpoint,
            user_id=parsed_uid,
        )
        await session.commit()

        # Update the in-memory UserSession cache so the browser tab picks
        # up the new DataStore without re-querying Postgres on the next read.
        state.datastore_credentials[datastore_id] = {
            "datastore_id": datastore_id,
            "datastore_name": datastore_name,
            "filespace_id": filespace_id,
            "filespace_name": filespace_name,
            "bucket_name": bucket_name,
            "region": region,
            "endpoint": endpoint,
        }
        state.s3_services[datastore_id] = s3_service
        state.selected_datastore = datastore_name
        state.log(f"Saved DataStore '{datastore_name}' for browsing")

        return templates.TemplateResponse(
            "partials/browser.html",
            {
                "request": request,
                "datastores": state.get_browsable_datastores(),
                "active_datastore_id": datastore_id,
            },
        )

    except Exception as e:
        await session.rollback()
        state.log(f"Failed to save credentials: {e}")
        return templates.TemplateResponse(
            "partials/connection_error.html",
            {
                "request": request,
                "error": f"Failed to connect to S3: {e}",
            },
        )


@app.delete("/api/datastores/{datastore_id}/credentials", response_class=HTMLResponse)
async def delete_datastore_credentials(
    request: Request,
    datastore_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Remove credentials for a DataStore."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)

    deleted = await state_helpers.delete_datastore_credentials(
        session, datastore_id, user_id=parsed_uid
    )
    await session.commit()

    if deleted:
        state.datastore_credentials.pop(datastore_id, None)
        state.s3_services.pop(datastore_id, None)
        state.log(f"Removed DataStore credentials for {datastore_id}")

    return await tab_settings(request, session=session)


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
        return templates.TemplateResponse(
            "partials/datastore_info_modal.html",
            {
                "request": request,
                "error": "DataStore or filespace not found",
            },
        )

    ll_client = LucidLinkClient(api_host=state.api_host)
    result = await ll_client.get_datastore(
        state.token, filespace_id, datastore_id, api_host=state.api_host
    )

    if isinstance(result, str):
        # Error occurred
        return templates.TemplateResponse(
            "partials/datastore_info_modal.html",
            {
                "request": request,
                "error": result,
            },
        )

    # Extract display data
    s3_params = result.get("s3StorageParams", {})

    return templates.TemplateResponse(
        "partials/datastore_info_modal.html",
        {
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
        },
    )


@app.delete("/api/datastores/{datastore_id}", response_class=HTMLResponse)
async def delete_datastore(
    request: Request,
    datastore_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Delete DataStore from LucidLink."""
    state = get_session_from_request(request)
    ds_repo = DatastoreCredentialsRepository(session)
    # Find filespace_id for this datastore
    filespace_id = None
    ds_name = None
    for name, info in state.datastores.items():
        if info.get("id") == datastore_id:
            ds_name = name
            filespace_id = state.filespaces.get(state.selected_filespace)
            break

    if not filespace_id:
        return templates.TemplateResponse(
            "partials/datastore_list.html",
            {
                "request": request,
                "datastores": [],
                "error": "DataStore or filespace not found",
            },
        )

    ll_client = LucidLinkClient(api_host=state.api_host)
    result = await ll_client.delete_datastore(
        state.token, filespace_id, datastore_id, api_host=state.api_host
    )

    if result != "SUCCESS":
        state.log(f"Error deleting DataStore: {result}")
        # Return current list with error
        datastores_data = []
        user_id = getattr(request.state, "user_id", None)
        parsed_uid = _parse_uid(user_id)
        for name, ds in state.datastores.items():
            s3_params = ds.get("s3StorageParams", {})
            ds_id = ds.get("id")
            has_creds = (
                await ds_repo.get_for_datastore_user(ds_id, parsed_uid) is not None
            )
            datastores_data.append(
                {
                    "id": ds_id,
                    "name": name,
                    "bucket": s3_params.get("bucketName", ""),
                    "has_credentials": has_creds,
                }
            )
        return templates.TemplateResponse(
            "partials/datastore_list.html",
            {
                "request": request,
                "datastores": datastores_data,
                "error": result,
            },
        )

    state.log(f"Deleted DataStore: {ds_name}")

    # Remove from local state
    if ds_name in state.datastores:
        del state.datastores[ds_name]

    # Also remove any saved credentials for this datastore (Postgres + cache)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid_for_delete = _parse_uid(user_id)
    deleted_creds = await state_helpers.delete_datastore_credentials(
        session, datastore_id, user_id=parsed_uid_for_delete
    )
    await session.commit()
    if deleted_creds:
        state.datastore_credentials.pop(datastore_id, None)
        state.s3_services.pop(datastore_id, None)

    ActivityLogger.app_datastore_deleted(user_id, ds_name)

    # Return updated list
    datastores_data = []
    parsed_uid = _parse_uid(user_id)
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        ds_id = ds.get("id")
        has_creds = await ds_repo.get_for_datastore_user(ds_id, parsed_uid) is not None
        datastores_data.append(
            {
                "id": ds_id,
                "name": name,
                "bucket": s3_params.get("bucketName", ""),
                "has_credentials": has_creds,
            }
        )

    return templates.TemplateResponse(
        "partials/datastore_list.html",
        {
            "request": request,
            "datastores": datastores_data,
            "success": f"DataStore '{ds_name}' deleted",
        },
    )


# ============== Create DataStore ==============


@app.get("/api/create-datastore-modal", response_class=HTMLResponse)
async def create_datastore_modal(request: Request):
    """Return the create datastore modal HTML."""
    _ = get_session_from_request(request)  # Verify authenticated
    return templates.TemplateResponse(
        "partials/create_datastore_modal.html",
        {
            "request": request,
        },
    )


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
    session: AsyncSession = Depends(get_db),
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
        return templates.TemplateResponse(
            "partials/create_datastore_error.html",
            {
                "request": request,
                "error": f"S3 access failed: {e}",
            },
        )
    except Exception as e:
        state.log(f"S3 validation error: {e}")
        return templates.TemplateResponse(
            "partials/create_datastore_error.html",
            {
                "request": request,
                "error": f"Failed to validate S3 access: {e}",
            },
        )

    ll_client = LucidLinkClient(api_host=state.api_host)
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
        api_host=state.api_host,
        use_virtual_addressing=virtual_addr,
        url_expiration_minutes=url_expiration_minutes or 10080,
    )

    if result == "SUCCESS":
        state.log(f"DataStore '{name}' created")

        # Reload datastores to get the new one's ID
        datastores = await ll_client.list_datastores(
            state.token, filespace_id, api_host=state.api_host
        )
        state.datastores = {}
        datastores_data = []
        new_datastore_id = None
        user_id = getattr(request.state, "user_id", None)
        parsed_uid = _parse_uid(user_id)
        ds_repo = DatastoreCredentialsRepository(session)

        for ds in datastores:
            ds_id = ds.get("id")
            ds_name = ds.get("name", ds_id)
            state.datastores[ds_name] = ds
            s3_params = ds.get("s3StorageParams", {})
            # For newly created datastore, credentials will be saved below
            # For others, check existing credentials
            has_creds = (ds_name == name) or (
                await ds_repo.get_for_datastore_user(ds_id, parsed_uid) is not None
            )
            datastores_data.append(
                {
                    "id": ds_id,
                    "name": ds_name,
                    "bucket": s3_params.get("bucketName", ""),
                    "has_credentials": has_creds,
                }
            )
            if ds_name == name:
                new_datastore_id = ds_id

        # Auto-save credentials for the new DataStore so the user lands in
        # the browser tab without re-entering keys.
        if new_datastore_id:
            await state_helpers.save_datastore_credentials(
                session,
                datastore_id=new_datastore_id,
                datastore_name=name,
                filespace_id=filespace_id,
                filespace_name=state.selected_filespace,
                bucket_name=bucket,
                access_key=access_key,
                secret_key=secret_key,
                region=region,
                endpoint=endpoint,
                user_id=parsed_uid,
            )
            await session.commit()
            state.datastore_credentials[new_datastore_id] = {
                "datastore_id": new_datastore_id,
                "datastore_name": name,
                "filespace_id": filespace_id,
                "filespace_name": state.selected_filespace,
                "bucket_name": bucket,
                "region": region,
                "endpoint": endpoint,
            }
            state.s3_services[new_datastore_id] = S3Service(
                access_key=access_key,
                secret_key=secret_key,
                region=region or "us-east-1",
                endpoint_url=endpoint or None,
            )

        # Log DataStore creation
        ActivityLogger.app_datastore_created(
            user_id, name, bucket, state.selected_filespace
        )

        # Return success with out-of-band swap to close modal and update list
        return templates.TemplateResponse(
            "partials/datastore_create_success.html",
            {
                "request": request,
                "datastores": datastores_data,
                "success": f"DataStore '{name}' created successfully",
            },
        )
    else:
        state.log(f"Error creating datastore: {result}")
        return templates.TemplateResponse(
            "partials/create_datastore_error.html",
            {
                "request": request,
                "error": result,
            },
        )


# ============== S3 Browser ==============


@app.get("/api/browse/{datastore_id}", response_class=HTMLResponse)
async def browse_datastore(
    request: Request,
    datastore_id: str,
    prefix: str = "",
    page: int = 1,
    page_size: int = 50,
):
    """Browse S3 for a specific DataStore."""
    from math import ceil

    state = get_session_from_request(request)
    cred = state.get_datastore_by_id(datastore_id)
    if not cred:
        return templates.TemplateResponse(
            "partials/connection_error.html",
            {
                "request": request,
                "error": "DataStore credentials not found. Please add credentials in Settings.",
            },
        )

    s3_service = state.get_s3_service_for_datastore(datastore_id)
    if not s3_service:
        return templates.TemplateResponse(
            "partials/connection_error.html",
            {
                "request": request,
                "error": "S3 service not initialized for this DataStore",
            },
        )

    try:
        bucket = cred.get("bucket_name")
        result = await s3_service.list_objects(bucket, prefix)

        # Pagination
        page_size = max(10, min(page_size, 200))
        total_items = result.total_count
        total_pages = max(1, ceil(total_items / page_size))
        page = max(1, min(page, total_pages))
        start = (page - 1) * page_size
        end = start + page_size
        page_items = result.items[start:end]

        return templates.TemplateResponse(
            "partials/datastore_browser.html",
            {
                "request": request,
                "datastore_id": datastore_id,
                "bucket": bucket,
                "prefix": prefix,
                "items": page_items,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "total_items": total_items,
                "is_truncated": result.is_truncated,
            },
        )

    except Exception as e:
        state.log(f"Browse error: {e}")
        return templates.TemplateResponse(
            "partials/connection_error.html",
            {
                "request": request,
                "error": str(e),
            },
        )


@app.get("/api/browse/{datastore_id}/back", response_class=HTMLResponse)
async def browse_datastore_back(
    request: Request,
    datastore_id: str,
    prefix: str = "",
    page_size: int = 50,
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

    return await browse_datastore(
        request, datastore_id, new_prefix, page=1, page_size=page_size
    )


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
            raise ValueError("DataStore credentials not found")

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
            state.log(
                f"Failed to create folder structure for: {key} - {structure_error}"
            )

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
    prefix: str = Form(""),
    datastore_id: str = Form(...),
):
    """Add a folder import job to the queue."""
    state = get_session_from_request(request)
    # Get DataStore credentials
    cred = state.get_datastore_by_id(datastore_id)
    if not cred:
        return templates.TemplateResponse(
            "partials/job_error.html",
            {
                "request": request,
                "error": "DataStore credentials not found",
            },
        )

    filespace_id = cred.get("filespace_id", "")
    bucket = cred.get("bucket_name", "")

    if not filespace_id or not datastore_id:
        return templates.TemplateResponse(
            "partials/job_error.html",
            {
                "request": request,
                "error": "Missing filespace or datastore configuration",
            },
        )

    if not bucket:
        return templates.TemplateResponse(
            "partials/job_error.html",
            {
                "request": request,
                "error": "No bucket specified",
            },
        )

    user_id = getattr(request.state, "user_id", None)
    job_id = await job_queue.add_job(
        bucket=bucket,
        prefix=prefix,
        filespace_id=filespace_id,
        datastore_id=datastore_id,
        user_id=user_id,
    )

    # Log job queued
    ActivityLogger.job_queued(user_id, job_id, prefix or "/")

    # Return updated job queue partial
    return templates.TemplateResponse(
        "partials/job_added.html",
        {
            "request": request,
            "job_id": job_id,
            "prefix": prefix,
        },
    )


# ============== Job Queue API ==============


@app.get("/api/jobs", response_class=HTMLResponse)
async def list_jobs(request: Request):
    """Get the job queue list."""
    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)
    jobs = await job_queue.get_jobs(user_id=user_id)
    status = await job_queue.get_queue_status(user_id=user_id)

    return templates.TemplateResponse(
        "partials/job_queue.html",
        {
            "request": request,
            "jobs": jobs,
            "queue_status": status,
        },
    )


@app.post("/api/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_job(request: Request, job_id: int):
    """Cancel a job."""
    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)
    await job_queue.cancel_job(job_id, user_id=user_id)
    jobs = await job_queue.get_jobs(user_id=user_id)
    status = await job_queue.get_queue_status(user_id=user_id)

    return templates.TemplateResponse(
        "partials/job_queue.html",
        {
            "request": request,
            "jobs": jobs,
            "queue_status": status,
        },
    )


@app.delete("/api/jobs/{job_id}", response_class=HTMLResponse)
async def delete_job(
    request: Request,
    job_id: int,
    session: AsyncSession = Depends(get_db),
):
    """Delete a job from history."""
    _ = get_session_from_request(request)  # Verify authenticated
    user_id_str = getattr(request.state, "user_id", None)
    parsed_user_id: Optional[uuid.UUID] = (
        uuid.UUID(user_id_str) if user_id_str else None
    )
    repo = JobRepository(session)
    await repo.delete_for_user(job_id, parsed_user_id)
    await session.commit()

    jobs = await job_queue.get_jobs(user_id=user_id_str)
    status = await job_queue.get_queue_status(user_id=user_id_str)

    return templates.TemplateResponse(
        "partials/job_queue.html",
        {
            "request": request,
            "jobs": jobs,
            "queue_status": status,
        },
    )


@app.post("/api/jobs/clear", response_class=HTMLResponse)
async def clear_jobs(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Clear completed jobs."""
    _ = get_session_from_request(request)  # Verify authenticated
    user_id_str = getattr(request.state, "user_id", None)
    parsed_user_id: Optional[uuid.UUID] = (
        uuid.UUID(user_id_str) if user_id_str else None
    )
    repo = JobRepository(session)
    await repo.clear_completed_for_user(parsed_user_id)
    await session.commit()

    jobs = await job_queue.get_jobs(user_id=user_id_str)
    status = await job_queue.get_queue_status(user_id=user_id_str)

    return templates.TemplateResponse(
        "partials/job_queue.html",
        {
            "request": request,
            "jobs": jobs,
            "queue_status": status,
        },
    )


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
        },
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
        },
    )


@app.post("/api/logs/clear", response_class=HTMLResponse)
async def clear_logs(request: Request):
    """Clear the activity log (per-user)."""
    state = get_session_from_request(request)
    state.logs.clear()
    return ""


# ============== Tabs ==============


@app.get("/api/tab/settings", response_class=HTMLResponse)
async def tab_settings(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Return settings tab content."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    await state.hydrate(session, parsed_uid)
    browsable_datastores = state.get_browsable_datastores()
    ds_repo = DatastoreCredentialsRepository(session)

    # Build datastores data for list view
    datastores_data = []
    for name, ds in state.datastores.items():
        s3_params = ds.get("s3StorageParams", {})
        ds_id = ds.get("id")
        # Check if user has credentials for this datastore
        has_creds = await ds_repo.get_for_datastore_user(ds_id, parsed_uid) is not None
        datastores_data.append(
            {
                "id": ds_id,
                "name": name,
                "bucket": s3_params.get("bucketName", ""),
                "has_credentials": has_creds,
            }
        )

    return templates.TemplateResponse(
        "partials/settings.html",
        {
            "request": request,
            "filespaces": list(state.filespaces.keys()) if state.filespaces else [],
            "datastores": datastores_data,
            "selected_filespace": state.selected_filespace,
            "selected_datastore": state.selected_datastore,
            "saved_token": state.token,
            "saved_api_host": state.api_host,
            "default_api_host": LL_HOST,
            "browsable_datastores": browsable_datastores,
        },
    )


@app.get("/api/tab/browser", response_class=HTMLResponse)
async def tab_browser(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Return browser tab content."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    await state.hydrate(session, parsed_uid)
    browsable_datastores = state.get_browsable_datastores()

    if browsable_datastores:
        return templates.TemplateResponse(
            "partials/browser.html",
            {
                "request": request,
                "datastores": browsable_datastores,
                "active_datastore_id": browsable_datastores[0]["datastore_id"]
                if browsable_datastores
                else None,
            },
        )

    return templates.TemplateResponse(
        "partials/not_connected.html",
        {
            "request": request,
        },
    )


@app.get("/api/tab/logs", response_class=HTMLResponse)
async def tab_logs(request: Request, subtab: str = "app"):
    """Return logs tab content with sub-tabs."""
    _ = get_session_from_request(request)  # Verify authenticated
    is_admin = getattr(request.state, "is_admin", False)

    # Validate subtab
    valid_subtabs = ["app", "jobs", "sqs"]
    if is_admin:
        valid_subtabs.append("admin")
    if subtab not in valid_subtabs:
        subtab = "app"

    return templates.TemplateResponse(
        "partials/logs_tab.html",
        {
            "request": request,
            "active_subtab": subtab,
            "is_admin": is_admin,
        },
    )


@app.get("/api/logs/app", response_class=HTMLResponse)
async def logs_app(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
):
    """Return application logs content."""
    from services.activity_logger import acount_logs, alist_logs

    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)

    logs = await alist_logs(
        session,
        category=ActivityLogger.APP,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    total = await acount_logs(session, category=ActivityLogger.APP, user_id=user_id)

    return templates.TemplateResponse(
        "partials/logs_app.html",
        {
            "request": request,
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "category": "app",
        },
    )


@app.get("/api/logs/jobs", response_class=HTMLResponse)
async def logs_jobs(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
):
    """Return jobs logs content."""
    from services.activity_logger import acount_logs, alist_logs

    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)

    logs = await alist_logs(
        session,
        category=ActivityLogger.JOB,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    total = await acount_logs(session, category=ActivityLogger.JOB, user_id=user_id)

    return templates.TemplateResponse(
        "partials/logs_jobs.html",
        {
            "request": request,
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "category": "jobs",
        },
    )


@app.get("/api/logs/sqs", response_class=HTMLResponse)
async def logs_sqs(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
):
    """Return SQS logs content."""
    from services.activity_logger import acount_logs, alist_logs

    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)

    logs = await alist_logs(
        session,
        category=ActivityLogger.SQS,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    total = await acount_logs(session, category=ActivityLogger.SQS, user_id=user_id)

    return templates.TemplateResponse(
        "partials/logs_sqs.html",
        {
            "request": request,
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "category": "sqs",
        },
    )


@app.get("/api/logs/admin", response_class=HTMLResponse)
async def logs_admin(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
):
    """Return admin activity logs (admin only)."""
    from services.activity_logger import acount_logs, alist_logs

    _ = get_session_from_request(request)  # Verify authenticated
    is_admin = getattr(request.state, "is_admin", False)

    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Admin view: show all users' admin logs.
    logs = await alist_logs(
        session,
        category=ActivityLogger.ADMIN,
        limit=limit,
        offset=offset,
        include_all_users=True,
    )
    total = await acount_logs(
        session, category=ActivityLogger.ADMIN, include_all_users=True
    )

    return templates.TemplateResponse(
        "partials/logs_admin.html",
        {
            "request": request,
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "category": "admin",
        },
    )


@app.post("/api/logs/clear/{category}", response_class=HTMLResponse)
async def clear_logs_category(
    request: Request,
    category: str,
    session: AsyncSession = Depends(get_db),
):
    """Clear logs by category."""
    from services.activity_logger import aclear_logs

    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)
    is_admin = getattr(request.state, "is_admin", False)

    valid_categories = ["app", "jobs", "sqs"]
    if is_admin:
        valid_categories.append("admin")

    if category not in valid_categories:
        raise HTTPException(status_code=400, detail="Invalid category")

    # Map URL category to database category
    db_category = category if category != "jobs" else "job"

    if category == "admin":
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        # Admin tab clears every user's admin-category logs.
        from sqlalchemy import delete as sa_delete

        from db.models import ActivityLog

        await session.execute(
            sa_delete(ActivityLog).where(ActivityLog.category == db_category)
        )
    else:
        await aclear_logs(session, category=db_category, user_id=user_id)
    await session.commit()

    # Return empty logs template for the category
    template_map = {
        "app": "partials/logs_app.html",
        "jobs": "partials/logs_jobs.html",
        "sqs": "partials/logs_sqs.html",
        "admin": "partials/logs_admin.html",
    }

    return templates.TemplateResponse(
        template_map[category],
        {
            "request": request,
            "logs": [],
            "total": 0,
            "limit": 100,
            "offset": 0,
            "category": category,
        },
    )


@app.get("/api/tab/help", response_class=HTMLResponse)
async def tab_help(request: Request):
    """Return help tab content."""
    state = get_session_from_request(request)
    return templates.TemplateResponse(
        "partials/help.html",
        {
            "request": request,
            "api_host": state.api_host,
        },
    )


def _user_to_template_dict(user: User) -> dict:
    """Serialize a User ORM row to the dict shape account_tab.html expects.

    The legacy SQLite-era contract returned `created_at` / `last_login` as
    ISO-8601 strings (TEXT columns), and the template slices them with
    `[:10]` to get the date prefix. CLAUDE.md rule #1 forbids touching
    `app/templates/**`, so we preserve the string shape here at the
    repo→view boundary.
    """
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }


@app.get("/api/tab/account", response_class=HTMLResponse)
async def tab_account(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Return account settings tab content."""
    user_id_str = getattr(request.state, "user_id", None)
    is_admin = getattr(request.state, "is_admin", False)

    repo = UserRepository(session)

    current_user_dict = None
    if user_id_str:
        user = await repo.get(uuid.UUID(user_id_str))
        if user is not None:
            current_user_dict = _user_to_template_dict(user)

    users_list: list[dict] = []
    if is_admin:
        rows = await repo.list(limit=1000, order_by=User.created_at)
        users_list = [_user_to_template_dict(u) for u in rows]

    return templates.TemplateResponse(
        "partials/account_tab.html",
        {
            "request": request,
            "current_user": current_user_dict,
            "users": users_list,
            "is_admin": is_admin,
        },
    )


# ============== SQS Event Stream API ==============


@app.get("/api/tab/sqs", response_class=HTMLResponse)
async def tab_sqs(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Return SQS tab content."""
    state = get_session_from_request(request)

    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    queue_repo = SqsQueueRepository(session)
    events_repo = SqsEventRepository(session)

    creds_row = await SqsCredentialsRepository(session).get_for_user(parsed_uid)
    queues_orm = list(await queue_repo.list_for_user(parsed_uid))
    events_orm = list(await events_repo.list_for_user(parsed_uid, limit=20))
    browsable_datastores = state.get_browsable_datastores()

    sqs_queues = await _enrich_queues_for_template(queues_orm, session, parsed_uid)
    sqs_events = [_event_to_dict(e) for e in events_orm]

    return templates.TemplateResponse(
        "partials/sqs_tab.html",
        {
            "request": request,
            "sqs_credentials": _creds_to_dict(creds_row),
            "sqs_queues": sqs_queues,
            "sqs_events": sqs_events,
            "browsable_datastores": browsable_datastores,
        },
    )


@app.post("/api/sqs/credentials", response_class=HTMLResponse)
async def save_sqs_credentials(
    request: Request,
    access_key: str = Form(...),
    secret_key: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
):
    """Save SQS IAM credentials. Per-user; one row per user."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    creds_repo = SqsCredentialsRepository(session)
    queue_repo = SqsQueueRepository(session)

    try:
        existing = await creds_repo.get_for_user(parsed_uid)

        # If no new secret key was supplied, the form is "edit access_key
        # only" — re-encrypt the existing plaintext (decrypt once via
        # state.get_sqs_credentials, then upsert with the new access_key).
        if not secret_key:
            if existing is None:
                raise ValueError("Secret key is required")
            existing_dict = await state_helpers.get_sqs_credentials(session, parsed_uid)
            assert existing_dict is not None
            secret_key = existing_dict["secret_key"]
        else:
            secret_key = secret_key.strip()
            if not secret_key:
                raise ValueError("Secret key is required")

        region = "us-east-1"
        await state_helpers.save_sqs_credentials(
            session, access_key, secret_key, region, user_id=parsed_uid
        )
        await session.commit()
        state.log("SQS credentials saved")
        ActivityLogger.sqs_credentials_saved(user_id, region)

        queues_orm = list(await queue_repo.list_for_user(parsed_uid))
        sqs_queues = await _enrich_queues_for_template(queues_orm, session, parsed_uid)
        browsable_datastores = state.get_browsable_datastores()
        creds_row = await creds_repo.get_for_user(parsed_uid)

        return templates.TemplateResponse(
            "partials/sqs_queue_list.html",
            {
                "request": request,
                "sqs_credentials": _creds_to_dict(creds_row),
                "sqs_queues": sqs_queues,
                "browsable_datastores": browsable_datastores,
            },
        )

    except Exception as e:
        await session.rollback()
        state.log(f"Failed to save SQS credentials: {e}")
        # Re-fetch creds on a fresh session-state to render the error response.
        creds_row = await creds_repo.get_for_user(parsed_uid)
        return templates.TemplateResponse(
            "partials/sqs_queue_list.html",
            {
                "request": request,
                "sqs_credentials": _creds_to_dict(creds_row),
                "sqs_queues": [],
                "browsable_datastores": state.get_browsable_datastores(),
                "error": str(e),
            },
        )


@app.delete("/api/sqs/credentials", response_class=HTMLResponse)
async def delete_sqs_credentials(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Delete SQS credentials."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)

    await state_helpers.delete_sqs_credentials(session, user_id=parsed_uid)
    await session.commit()
    state.log("SQS credentials removed")
    ActivityLogger.sqs_credentials_deleted(user_id)

    return await tab_sqs(request, session=session)


@app.get("/api/sqs/iam-policy", response_class=HTMLResponse)
async def sqs_iam_policy_modal(request: Request):
    """Show modal with example IAM policy for SQS."""
    return templates.TemplateResponse(
        "partials/sqs_iam_policy_modal.html",
        {
            "request": request,
        },
    )


@app.get("/api/sqs/queues/add-modal", response_class=HTMLResponse)
async def sqs_add_queue_modal(request: Request):
    """Show modal for adding a new SQS queue."""
    state = get_session_from_request(request)
    browsable_datastores = state.get_browsable_datastores()

    return templates.TemplateResponse(
        "partials/sqs_queue_modal.html",
        {
            "request": request,
            "browsable_datastores": browsable_datastores,
        },
    )


@app.get("/api/sqs/queues/discover", response_class=HTMLResponse)
async def discover_sqs_queues(
    request: Request,
    queue_region: str = "us-east-1",
    session: AsyncSession = Depends(get_db),
):
    """Discover existing SQS queues in a region. Returns HTML options for select."""
    from services.sqs_service import SQSService, SQSError

    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)

    try:
        sqs_creds = await state_helpers.get_sqs_credentials(session, parsed_uid)
        if not sqs_creds:
            return HTMLResponse('<option value="">No credentials configured</option>')

        access_key = sqs_creds["access_key"]
        secret_key = sqs_creds["secret_key"]

        if not access_key or not secret_key:
            return HTMLResponse('<option value="">Invalid credentials</option>')

        # List queues in the selected region
        sqs = SQSService(access_key, secret_key, queue_region)
        queues = sqs.list_queues()

        if not queues:
            return HTMLResponse(
                f'<option value="">No queues found in {queue_region}</option>'
            )

        # Build HTML options
        options = ['<option value="">Select a queue...</option>']
        for q in queues:
            options.append(f'<option value="{q["url"]}">{q["name"]}</option>')

        return HTMLResponse("\n".join(options))

    except SQSError as e:
        return HTMLResponse(f'<option value="">Error: {str(e)}</option>')
    except Exception:
        return HTMLResponse('<option value="">Error discovering queues</option>')


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
    session: AsyncSession = Depends(get_db),
):
    """Add or create a new SQS queue configuration."""
    state = get_session_from_request(request)
    from services.sqs_service import SQSService, SQSError

    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    browsable_datastores = state.get_browsable_datastores()
    ds_repo = DatastoreCredentialsRepository(session)
    queue_repo = SqsQueueRepository(session)

    try:
        sqs_creds = await state_helpers.get_sqs_credentials(session, parsed_uid)
        if not sqs_creds:
            raise ValueError("SQS credentials not configured")

        access_key = sqs_creds["access_key"]
        secret_key = sqs_creds["secret_key"]

        if not access_key or not secret_key:
            raise ValueError("Invalid SQS credentials")

        # Get DataStore credentials to validate and get bucket name
        ds_cred = await ds_repo.get_for_datastore_user(datastore_id, parsed_uid)
        if not ds_cred:
            raise ValueError(
                "DataStore credentials not found. Please add credentials in Settings first."
            )

        filespace_id = ds_cred.filespace_id or ""
        bucket_name = ds_cred.bucket_name or ""

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

            if not re.match(r"^[a-zA-Z0-9_-]+$", queue_name):
                raise ValueError(
                    "Queue name can only contain alphanumeric characters, hyphens, and underscores"
                )

            queue_info = sqs.create_queue(queue_name)
            state.log(f"Created SQS queue '{queue_name}' in AWS ({region})")

            # Configure queue policy and S3 bucket notifications
            if configure_s3_policy == "true" and bucket_name:
                # 1. Set queue policy to allow S3 to send messages
                sqs.configure_queue_for_s3(queue_info["url"], bucket_name)
                state.log(f"Configured queue policy for S3 bucket '{bucket_name}'")

                # 2. Configure S3 bucket to send notifications to the queue
                from services.sqs_service import (
                    S3NotificationService,
                    S3NotificationError,
                )

                s3_notif = S3NotificationService(access_key, secret_key, region)
                try:
                    s3_notif.add_sqs_notification(
                        bucket_name=bucket_name,
                        queue_arn=queue_info["arn"],
                        notification_id=f"LucidLink-{queue_name}",
                    )
                    state.log(
                        f"Configured S3 bucket '{bucket_name}' to send events to queue"
                    )
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

            region_match = re.search(
                r"sqs\.([a-z0-9-]+)\.amazonaws\.com", normalized_url
            )
            if region_match:
                region = region_match.group(1)
            else:
                region = queue_region  # Use form value as fallback

            # Initialize SQS service with the correct region
            sqs = SQSService(access_key, secret_key, region)

            # Validate queue and get info
            queue_info = sqs.validate_queue_url(normalized_url)

        # Create queue record (UUID PK auto-generated by the model default)
        new_queue = await queue_repo.create(
            queue_url=queue_info["url"],
            queue_arn=queue_info["arn"],
            name=queue_info["name"],
            region=queue_info["region"],
            datastore_id=datastore_id,
            filespace_id=filespace_id,
            import_prefix=import_prefix.strip(),
            status="active",
            user_id=parsed_uid,
        )
        await session.commit()

        action = "created" if mode == "create" else "added"
        state.log(f"SQS queue '{queue_info['name']}' {action} for automatic imports")
        ActivityLogger.sqs_queue_created(user_id, queue_info["name"], str(new_queue.id))

        # Return updated queue list
        queues_orm = list(await queue_repo.list_for_user(parsed_uid))
        sqs_queues = await _enrich_queues_for_template(queues_orm, session, parsed_uid)

        return templates.TemplateResponse(
            "partials/sqs_queue_list.html",
            {
                "request": request,
                "sqs_credentials": _creds_to_dict(
                    await SqsCredentialsRepository(session).get_for_user(parsed_uid)
                ),
                "sqs_queues": sqs_queues,
                "browsable_datastores": browsable_datastores,
                "success": f"Queue '{queue_info['name']}' {action} successfully",
            },
        )

    except SQSError as e:
        return templates.TemplateResponse(
            "partials/sqs_queue_modal.html",
            {
                "request": request,
                "browsable_datastores": browsable_datastores,
                "error": f"SQS error: {e}",
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "partials/sqs_queue_modal.html",
            {
                "request": request,
                "browsable_datastores": browsable_datastores,
                "error": str(e),
            },
        )


@app.delete("/api/sqs/queues/{queue_id}", response_class=HTMLResponse)
async def delete_sqs_queue(
    request: Request,
    queue_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Delete an SQS queue configuration and clean up AWS resources."""
    state = get_session_from_request(request)
    from services.sqs_service import (
        SQSService,
        S3NotificationService,
        SQSError,
        S3NotificationError,
    )

    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    parsed_qid = uuid.UUID(queue_id)
    ds_repo = DatastoreCredentialsRepository(session)
    queue_repo = SqsQueueRepository(session)
    creds_repo = SqsCredentialsRepository(session)

    queue = await queue_repo.get_for_user(parsed_qid, parsed_uid)
    if queue is None:
        queues_orm = list(await queue_repo.list_for_user(parsed_uid))
        sqs_queues = await _enrich_queues_for_template(queues_orm, session, parsed_uid)
        return templates.TemplateResponse(
            "partials/sqs_queue_list.html",
            {
                "request": request,
                "sqs_credentials": _creds_to_dict(
                    await creds_repo.get_for_user(parsed_uid)
                ),
                "sqs_queues": sqs_queues,
                "browsable_datastores": state.get_browsable_datastores(),
                "error": "Queue not found",
            },
        )

    queue_name = queue.name or queue_id
    queue_url = queue.queue_url
    queue_arn = queue.queue_arn
    queue_region = queue.region or "us-east-1"
    datastore_id = queue.datastore_id

    # AWS cleanup uses decrypted SQS credentials.
    sqs_creds_dict = await state_helpers.get_sqs_credentials(session, parsed_uid)
    cleanup_errors: list[str] = []

    if sqs_creds_dict and queue_url:
        access_key = sqs_creds_dict["access_key"]
        secret_key = sqs_creds_dict["secret_key"]

        if access_key and secret_key:
            # 1. Remove S3 bucket notification
            if queue_arn and datastore_id:
                ds_cred = await ds_repo.get_for_datastore_user(datastore_id, parsed_uid)
                if ds_cred:
                    bucket_name = ds_cred.bucket_name
                    if bucket_name:
                        try:
                            s3_notif = S3NotificationService(
                                access_key, secret_key, queue_region
                            )
                            s3_notif.remove_sqs_notification(bucket_name, queue_arn)
                            state.log(
                                f"Removed S3 notification from bucket '{bucket_name}'"
                            )
                        except S3NotificationError as e:
                            cleanup_errors.append(f"S3 notification: {e}")

            # 2. Delete SQS queue from AWS
            try:
                sqs = SQSService(access_key, secret_key, queue_region)
                sqs.delete_queue(queue_url)
                state.log(f"Deleted SQS queue '{queue_name}' from AWS")
            except SQSError as e:
                cleanup_errors.append(f"SQS queue: {e}")

    await queue_repo.delete_for_user(parsed_qid, parsed_uid)
    await session.commit()

    if cleanup_errors:
        state.log(
            f"Queue '{queue_name}' removed (some AWS cleanup failed: {'; '.join(cleanup_errors)})"
        )
    else:
        state.log(f"Queue '{queue_name}' fully deleted")

    ActivityLogger.sqs_queue_deleted(user_id, queue_name, queue_id)

    queues_orm = list(await queue_repo.list_for_user(parsed_uid))
    sqs_queues = await _enrich_queues_for_template(queues_orm, session, parsed_uid)

    return templates.TemplateResponse(
        "partials/sqs_queue_list.html",
        {
            "request": request,
            "sqs_credentials": _creds_to_dict(
                await creds_repo.get_for_user(parsed_uid)
            ),
            "sqs_queues": sqs_queues,
            "browsable_datastores": state.get_browsable_datastores(),
            "success": f"Queue '{queue_name}' deleted",
        },
    )


@app.post("/api/sqs/queues/{queue_id}/pause", response_class=HTMLResponse)
async def pause_sqs_queue(
    request: Request,
    queue_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Pause polling for a queue."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    parsed_qid = uuid.UUID(queue_id)
    queue_repo = SqsQueueRepository(session)

    await queue_repo.update_status_for_user(parsed_qid, parsed_uid, status="paused")
    await session.commit()

    queue = await queue_repo.get_for_user(parsed_qid, parsed_uid)
    queue_name = (queue.name if queue else queue_id) or queue_id
    state.log(f"SQS queue '{queue_name}' paused")

    queues_orm = list(await queue_repo.list_for_user(parsed_uid))
    sqs_queues = await _enrich_queues_for_template(queues_orm, session, parsed_uid)

    return templates.TemplateResponse(
        "partials/sqs_queue_list.html",
        {
            "request": request,
            "sqs_credentials": _creds_to_dict(
                await SqsCredentialsRepository(session).get_for_user(parsed_uid)
            ),
            "sqs_queues": sqs_queues,
            "browsable_datastores": state.get_browsable_datastores(),
        },
    )


@app.post("/api/sqs/queues/{queue_id}/resume", response_class=HTMLResponse)
async def resume_sqs_queue(
    request: Request,
    queue_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Resume polling for a queue."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    parsed_qid = uuid.UUID(queue_id)
    queue_repo = SqsQueueRepository(session)

    await queue_repo.update_status_for_user(
        parsed_qid, parsed_uid, status="active", error_message=""
    )
    await session.commit()

    queue = await queue_repo.get_for_user(parsed_qid, parsed_uid)
    queue_name = (queue.name if queue else queue_id) or queue_id
    state.log(f"SQS queue '{queue_name}' resumed")

    queues_orm = list(await queue_repo.list_for_user(parsed_uid))
    sqs_queues = await _enrich_queues_for_template(queues_orm, session, parsed_uid)

    return templates.TemplateResponse(
        "partials/sqs_queue_list.html",
        {
            "request": request,
            "sqs_credentials": _creds_to_dict(
                await SqsCredentialsRepository(session).get_for_user(parsed_uid)
            ),
            "sqs_queues": sqs_queues,
            "browsable_datastores": state.get_browsable_datastores(),
        },
    )


@app.get("/api/sqs/queues/{queue_id}/info", response_class=HTMLResponse)
async def sqs_queue_info(
    request: Request,
    queue_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Get queue details and statistics."""
    _ = get_session_from_request(request)  # Verify authenticated
    from services.sqs_service import SQSService

    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    parsed_qid = uuid.UUID(queue_id)
    queue_repo = SqsQueueRepository(session)
    events_repo = SqsEventRepository(session)
    ds_repo = DatastoreCredentialsRepository(session)

    queue_orm = await queue_repo.get_for_user(parsed_qid, parsed_uid)
    if queue_orm is None:
        return templates.TemplateResponse(
            "partials/sqs_queue_info.html",
            {
                "request": request,
                "error": "Queue not found",
            },
        )

    queue = _queue_to_dict(queue_orm)
    queue["events_today"] = await events_repo.count_today_for_queue(
        parsed_qid, parsed_uid
    )

    cred = await ds_repo.get_for_datastore_user(queue_orm.datastore_id, parsed_uid)
    if cred is not None:
        queue["datastore_name"] = cred.datastore_name or ""
        queue["filespace_name"] = cred.filespace_name or ""

    # Best-effort: surface AWS-side queue depth if we can reach it.
    try:
        sqs_creds = await state_helpers.get_sqs_credentials(session, parsed_uid)
        if sqs_creds:
            access_key = sqs_creds["access_key"]
            secret_key = sqs_creds["secret_key"]
            region = sqs_creds.get("region") or "us-east-1"
            if access_key and secret_key:
                sqs = SQSService(access_key, secret_key, region)
                attrs = sqs.get_queue_attributes(queue["queue_url"])
                queue["approximate_messages"] = attrs.get(
                    "ApproximateNumberOfMessages", "0"
                )
    except Exception:
        pass

    return templates.TemplateResponse(
        "partials/sqs_queue_info.html",
        {
            "request": request,
            "queue": queue,
        },
    )


@app.get("/api/sqs/events", response_class=HTMLResponse)
async def list_sqs_events(
    request: Request,
    limit: int = 50,
    session: AsyncSession = Depends(get_db),
):
    """List recent SQS events."""
    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    events_orm = await SqsEventRepository(session).list_for_user(
        parsed_uid, limit=limit
    )
    events = [_event_to_dict(e) for e in events_orm]

    return templates.TemplateResponse(
        "partials/sqs_events.html",
        {
            "request": request,
            "sqs_events": events,
        },
    )


@app.get("/api/sqs/events/content", response_class=HTMLResponse)
async def list_sqs_events_content(
    request: Request,
    limit: int = 50,
    session: AsyncSession = Depends(get_db),
):
    """List recent SQS events - inner content only for polling."""
    _ = get_session_from_request(request)  # Verify authenticated
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)
    events_orm = await SqsEventRepository(session).list_for_user(
        parsed_uid, limit=limit
    )
    events = [_event_to_dict(e) for e in events_orm]

    return templates.TemplateResponse(
        "partials/sqs_events_content.html",
        {
            "request": request,
            "sqs_events": events,
        },
    )


@app.delete("/api/sqs/events", response_class=HTMLResponse)
async def clear_sqs_events(
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Clear all SQS event history for the current user."""
    state = get_session_from_request(request)
    user_id = getattr(request.state, "user_id", None)
    parsed_uid = _parse_uid(user_id)

    deleted = await SqsEventRepository(session).clear_for_user(parsed_uid)
    await session.commit()
    state.log(f"Cleared {deleted} SQS events")

    return templates.TemplateResponse(
        "partials/sqs_events_content.html",
        {
            "request": request,
            "sqs_events": [],
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

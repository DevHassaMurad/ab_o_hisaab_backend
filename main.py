from fastapi import FastAPI,Depends,APIRouter
import os
import uvicorn
from routes import auth_routes, campaign_routes, donation_routes, donor_ledger_routes, donor_report_routes, fund_routes, health_routes, ngo_routes, notification_routes
from routes import auth_routes, health_routes, registration_routes, document_routes, verification_routes
from fastapi.middleware.cors import CORSMiddleware
from config.document_storage import UploadSizeLimit
from middlewares.auth_middleware import auth_middleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from controller.authentication import AuthFlowError
import asyncio
from contextlib import asynccontextmanager

from config.database import engine
from config.outbox import outbox_worker


@asynccontextmanager
async def lifespan(_app):
    # Deliver queued emails (and retry failed ones) for the life of the process.
    task = None if os.environ.get("DISABLE_OUTBOX_WORKER") else asyncio.create_task(outbox_worker(engine))
    # Campaign activation and expiry (scheduled -> active, ended -> completed), once a minute.
    sched = None if os.environ.get("DISABLE_OUTBOX_WORKER") else asyncio.create_task(campaign_scheduler())
    yield
    for t in (task, sched):
        if t:
            t.cancel()


async def campaign_scheduler():
    from config.database import SessionLocal
    from controller.campaigns import run_schedule
    from controller.donation import push_notification
    while True:
        try:
            with SessionLocal() as db:
                notes = await asyncio.to_thread(run_schedule, db)
            for user_id, payload in notes:
                await push_notification(user_id, payload)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Campaign schedule run failed.")
        await asyncio.sleep(60)


app = FastAPI(lifespan=lifespan)
app = FastAPI()
app.add_middleware(UploadSizeLimit)
app.add_middleware(CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if origin.strip()],
    allow_credentials=False, allow_methods=['GET', 'POST', 'PATCH', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type', 'X-Verification-Token', 'X-Scenario-Id'])
api_router = APIRouter(prefix="/api")

#  Public Routes that can have both public and protected routes
api_router.include_router(auth_routes.router)
api_router.include_router(registration_routes.router)
api_router.include_router(document_routes.router)
api_router.include_router(verification_routes.router)
api_router.include_router(health_routes.router)
api_router.include_router(donation_routes.router)
api_router.include_router(notification_routes.router)
api_router.include_router(fund_routes.router)
api_router.include_router(donor_report_routes.router)
api_router.include_router(donor_ledger_routes.router)
api_router.include_router(ngo_routes.router)
api_router.include_router(campaign_routes.router)
app.include_router(api_router)
# Keep the existing portal's auth paths compatible with the MySQL backend.
app.include_router(auth_routes.router, prefix="/api/v1", include_in_schema=False)
app.include_router(registration_routes.router, prefix="/api/v1", include_in_schema=False)
app.include_router(document_routes.router, prefix="/api/v1", include_in_schema=False)
app.include_router(verification_routes.router, prefix="/api/v1", include_in_schema=False)

@app.on_event('startup')
def recover_document_jobs():
    from controller.verification import recover_interrupted_jobs
    recover_interrupted_jobs()

@app.exception_handler(AuthFlowError)
async def auth_flow_error(request, exc):
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Do not echo passwords, verification codes or reset tokens in validation errors.
    details = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
    return JSONResponse({"detail": details}, status_code=422)

if __name__ == "__main__":
    uvicorn.run("main:app", host=os.environ.get("HOST", "localhost"),
                port=int(os.environ.get("PORT", "8000")))

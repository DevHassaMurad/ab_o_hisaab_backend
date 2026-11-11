from fastapi import FastAPI,Depends,APIRouter
import os
import uvicorn
from routes import auth_routes
from middlewares.auth_middleware import auth_middleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from controller.authentication import AuthFlowError
app = FastAPI()
api_router = APIRouter(prefix="/api")

#  Public Routes that can have both public and protected routes
api_router.include_router(auth_routes.router)
app.include_router(api_router)
# Keep the existing portal's auth paths compatible with the MySQL backend.
app.include_router(auth_routes.router, prefix="/api/v1", include_in_schema=False)

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
                port=int(os.environ.get("PORT", "5800")))

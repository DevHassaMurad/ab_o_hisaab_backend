from fastapi import FastAPI,Depends,APIRouter
import os
import uvicorn
from routes import auth_routes, health_routes
from middlewares.auth_middleware import auth_middleware
app = FastAPI()
api_router = APIRouter(prefix="/api")

#  Public Routes that can have both public and protected routes
api_router.include_router(auth_routes.router)
api_router.include_router(health_routes.router)
app.include_router(api_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host=os.environ.get("HOST", "localhost"),
                port=int(os.environ.get("PORT", "8000")))

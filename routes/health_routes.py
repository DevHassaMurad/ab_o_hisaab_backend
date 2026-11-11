from fastapi import APIRouter

router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


@router.get("")
def health():
    """Public liveness check used by the portal's sign-in footer."""
    return {"status": "ok"}

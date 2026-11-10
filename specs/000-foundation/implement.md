# Implementation Guide: 000 Foundation

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T006 | Repo boots, config loads |
| 2 | T007–T014 | All tables migrate on PostGIS |
| 3 | T015–T018 | Interfaces, stubs and test harness ready → **unblocks all FRs** |
| 4 | T019–T023 | Sign-in plus RBAC |
| 5 | T029–T030 | Encryption |
| 6 | T024–T028 | Seeded geography and funds |
| 7 | T031–T034 | Scenario isolation and labels |
| 8 | T035–T037 | Pipeline skeleton and polish |

## Key code

### Encrypted column and phone hash (`app/core/crypto.py`)
```python
import hashlib, re
from cryptography.fernet import Fernet
from sqlalchemy.types import TypeDecorator, String
from app.core.config import settings

_f = Fernet(settings.FERNET_KEY)

class EncryptedString(TypeDecorator):
    impl = String
    cache_ok = True
    def process_bind_param(self, value, dialect):
        return None if value is None else _f.encrypt(value.encode()).decode()
    def process_result_value(self, value, dialect):
        return None if value is None else _f.decrypt(value.encode()).decode()

def normalise_pk_phone(raw: str) -> str:
    d = re.sub(r"\D", "", raw.replace("whatsapp:", ""))
    if d.startswith("0092"): d = d[4:]
    elif d.startswith("92"): d = d[2:]
    elif d.startswith("0"): d = d[1:]
    if not re.fullmatch(r"3\d{9}", d):
        raise ValueError("not a Pakistani mobile number")
    return "+92" + d

def phone_hash(raw: str) -> str:
    return hashlib.sha256((normalise_pk_phone(raw) + settings.PHONE_HASH_SALT).encode()).hexdigest()
```

### Scenario scope (`app/core/scenario.py`)
```python
from contextvars import ContextVar
from uuid import UUID
from fastapi import Header

_current: ContextVar[UUID | None] = ContextVar("scenario_id", default=None)

def current_scenario() -> UUID | None: return _current.get()

class use_scenario:                       # with use_scenario(sid): ...
    def __init__(self, sid): self.sid = sid
    def __enter__(self): self.tok = _current.set(self.sid)
    def __exit__(self, *a): _current.reset(self.tok)

async def get_scenario(x_scenario_id: UUID | None = Header(default=None)):
    _current.set(x_scenario_id); return x_scenario_id

def scoped(stmt, model):
    sid = current_scenario()
    return stmt.where(model.scenario_id.is_(None) if sid is None else model.scenario_id == sid)
```

### Role guard (`app/core/security.py`)
```python
def require_role(*roles: Role):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise Forbidden(code="ROLE_FORBIDDEN")
        return user
    dep.__roles__ = roles            # read by the route-table contract test
    return dep

def assert_district_access(user: User, district_id: UUID) -> None:
    if user.role == Role.DC and user.district_id != district_id:
        raise Forbidden(code="DISTRICT_FORBIDDEN")
```

### DonorScope (`app/schemas/common.py`)
```python
PII_DENY = {"name", "phone", "cnic", "wallet_id", "beneficiary_id", "lat", "lon"}

class DonorScope(BaseModel):
    @classmethod
    def __pydantic_init_subclass__(cls, **kw):
        super().__pydantic_init_subclass__(**kw)
        bad = PII_DENY & set(cls.model_fields)
        if bad:
            raise TypeError(f"{cls.__name__} exposes PII fields {bad}")
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://aab:aab@localhost:5432/aab` | |
| `JWT_SECRET` / `JWT_TTL_MIN` | — / 60 | |
| `FERNET_KEY` | — | Generate with `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |
| `PHONE_HASH_SALT` | — | Never rotate it after data exists |
| `PIPELINE_CRON` | `0 1 * * *` (UTC) | 06:00 PKT |
| `FLAGS` | `""` | Comma list, e.g. `FR13_LLM,FR16_FALLBACK` |

## Commands

```bash
docker compose up -d db
pip install -e ".[dev]"
alembic upgrade head
python scripts/seed.py --provinces SINDH KP
uvicorn app.main:create_app --factory --reload
pytest -q tests/unit/core tests/integration/test_auth.py
ruff check . && mypy app/models app/services
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1..4 | `tests/integration/test_auth.py` |
| SC-001 | `tests/contract/test_route_roles.py` |
| US2-1..3, SC-003 | `tests/integration/test_seed.py` |
| US3-1..2, SC-002 | `tests/unit/core/test_crypto.py` plus manual `psql -c "select phone from beneficiaries limit 1"` |
| US4 | `tests/integration/test_scenario_scope.py` |
| US5 | `tests/integration/test_labels.py` |

## Demo notes
- Before the pitch, log in once as each role and keep the tokens in the frontend.
- The `X-Simulated-Data` header lets the frontend show a "SIMULATED" ribbon on every demo screen (NFR13).

## Pitfalls
- Windows dev: GeoAlchemy2 needs no native libs, but shapely does. Use the wheels, not conda mixes.
- Don't make the APScheduler start twice under `uvicorn --reload`. Guard it with an env check or start it only in the main process.
- Alembic autogenerate misses PostGIS extension and enum changes, so hand-edit `0001`.

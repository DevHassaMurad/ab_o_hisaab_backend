from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
import os
load_dotenv()

if(os.environ.get("DATABASE_DIALECT") == "postgresql"):
    driver = "postgresql"
elif(os.environ.get("DATABASE_DIALECT") == "mysql"):
    driver = "mysql+pymysql"
else:
    raise Exception("Database dialect not supported. Please use either 'postgresql' or 'mysql'.")
Database_URL = URL.create(
    drivername=driver,
    username=os.environ.get("DATABASE_USER"),
    password=os.environ.get("DATABASE_PASSWORD"),
    host=os.environ.get("DATABASE_HOST") or "localhost",
    port=int(os.environ["DATABASE_PORT"]) if os.environ.get("DATABASE_PORT") else None,
    database=os.environ.get("DATABASE_NAME"),
)
engine = create_engine(Database_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

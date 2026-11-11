from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from config.database import Base


class Role(Base):
    __tablename__ = "roles"

    # IDs are assigned explicitly so ID 0 works on MySQL too.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)

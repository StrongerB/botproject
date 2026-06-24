from sqlalchemy import String, Intenger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class CompanyFile(Mapped):
    __tablename__='company_files'
    id: Mapped[int]=mapped_column(primary_key=True,autoincrement=True)
    file_key: Mapped[str]=mapped_column(String(50), unique=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[str] = mapped_column(String(255))
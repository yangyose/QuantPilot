from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    def __repr__(self) -> str:
        pk_col = self.__table__.primary_key.columns.keys()[0]
        return f"<{self.__class__.__name__} {pk_col}={getattr(self, pk_col)!r}>"

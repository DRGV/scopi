"""Results table backed by a pandas DataFrame."""

from __future__ import annotations

import pandas as pd


class ResultsTable:
    def __init__(self, title: str = "Results"):
        self.title = title
        self._rows: list[dict] = []
        self.listeners: list = []

    def add_row(self, **values) -> None:
        self._rows.append(values)
        self._notify()

    def add_rows(self, rows) -> None:
        self._rows.extend(dict(r) for r in rows)
        self._notify()

    def clear(self) -> None:
        self._rows.clear()
        self._notify()

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def df(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows)

    def set_df(self, df: pd.DataFrame) -> None:
        self._rows = df.to_dict("records")
        self._notify()

    def to_csv(self, path: str) -> None:
        self.df.to_csv(path, index=False)

    @classmethod
    def from_csv(cls, path: str, title: str = "Results") -> "ResultsTable":
        t = cls(title)
        t._rows = pd.read_csv(path).to_dict("records")
        return t

    def summary(self) -> pd.DataFrame:
        """Descriptive statistics of numeric columns."""
        df = self.df.select_dtypes("number")
        return df.describe().T if not df.empty else df

    def filter(self, query: str) -> pd.DataFrame:
        return self.df.query(query)

    def group_stats(self, by: str, agg=("count", "mean", "std", "min", "max")) -> pd.DataFrame:
        return self.df.groupby(by).agg(list(agg))

    def _notify(self) -> None:
        for f in list(self.listeners):
            f(self)

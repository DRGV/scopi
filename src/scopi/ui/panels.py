# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Side panels: histogram / brightness-contrast, results table, ROI manager."""

from __future__ import annotations

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QTableView, QVBoxLayout, QWidget, QDoubleSpinBox)

from ..core import luts
from ..core.results import ResultsTable
from . import render


class HistogramWidget(QWidget):
    range_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.doc = None
        self.log = False
        self._hist = None
        self._lo = self._hi = 0.0
        self._plane_token = None

    def set_doc(self, doc) -> None:
        self.doc = doc
        self._plane_token = None
        self.refresh()

    def refresh(self) -> None:
        d = self.doc
        if d is None:
            self._hist = None
        else:
            lo, hi = d.full_range() if d.dtype.kind in "ui" else (float(d.data.min()), float(d.data.max()))
            if d.dtype == np.uint16:
                # use the occupied range for 16-bit so the histogram is not a single spike
                f = d.gray_frame()
                lo, hi = float(f.min()), float(f.max())
            self._lo, self._hi = lo, hi
            self._hist = render.histogram(d.gray_frame(), lo, hi)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect().adjusted(2, 2, -2, -2)
        p.fillRect(self.rect(), self.palette().base())
        if self._hist is None or self.doc is None:
            return
        h = self._hist.astype(float)
        if self.log:
            h = np.log1p(h)
        m = h.max() or 1.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(120, 120, 120))
        n = len(h)
        bw = r.width() / n
        for i, v in enumerate(h):
            bh = r.height() * v / m
            p.drawRect(QRectF(r.left() + i * bw, r.bottom() - bh, max(bw, 1), bh))
        lo, hi = self.doc.display_range
        span = (self._hi - self._lo) or 1.0
        for v in (lo, hi):
            x = r.left() + (v - self._lo) / span * r.width()
            p.setPen(QPen(QColor(255, 80, 80), 1.5))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

    def mouseMoveEvent(self, e):
        self._drag(e)

    def mousePressEvent(self, e):
        self._drag(e)

    def _drag(self, e) -> None:
        if self.doc is None or self._hist is None:
            return
        r = self.rect().adjusted(2, 2, -2, -2)
        v = self._lo + (e.position().x() - r.left()) / max(r.width(), 1) * (self._hi - self._lo)
        lo, hi = self.doc.display_range
        if abs(v - lo) <= abs(v - hi):
            lo = min(v, hi - 1e-6)
        else:
            hi = max(v, lo + 1e-6)
        self.range_changed.emit(lo, hi)


class DisplayPanel(QWidget):
    """Brightness/contrast: histogram, min/max, auto/reset, LUT selection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        lay = QVBoxLayout(self)
        self.hist = HistogramWidget()
        self.hist.range_changed.connect(self._set_range)
        lay.addWidget(self.hist)
        row = QHBoxLayout()
        self.min_spin, self.max_spin = QDoubleSpinBox(), QDoubleSpinBox()
        for s in (self.min_spin, self.max_spin):
            s.setRange(-1e12, 1e12)
            s.setDecimals(3)
            s.editingFinished.connect(self._spin_changed)
        row.addWidget(QLabel("Min"))
        row.addWidget(self.min_spin)
        row.addWidget(QLabel("Max"))
        row.addWidget(self.max_spin)
        lay.addLayout(row)
        row = QHBoxLayout()
        auto, reset = QPushButton("Auto"), QPushButton("Reset")
        auto.clicked.connect(self._auto)
        reset.clicked.connect(self._reset)
        self.log_box = QCheckBox("Log")
        self.log_box.toggled.connect(self._set_log)
        row.addWidget(auto)
        row.addWidget(reset)
        row.addWidget(self.log_box)
        lay.addLayout(row)
        self.lut_combo = QComboBox()
        self._luts = luts.all_luts()
        self.lut_combo.addItems(list(self._luts))
        self.lut_combo.currentTextChanged.connect(self._set_lut)
        self.invert_box = QCheckBox("Invert LUT")
        self.invert_box.toggled.connect(lambda _: self._set_lut(self.lut_combo.currentText()))
        lay.addWidget(QLabel("Look-up table"))
        lay.addWidget(self.lut_combo)
        lay.addWidget(self.invert_box)
        lay.addStretch(1)

    def set_doc(self, doc) -> None:
        if self.doc is not None and self._on_doc in self.doc.listeners:
            self.doc.listeners.remove(self._on_doc)
        self.doc = doc
        self.setEnabled(doc is not None)
        self.hist.set_doc(doc)
        if doc is not None:
            doc.listeners.append(self._on_doc)
            self._sync()

    def _on_doc(self, doc, what):
        if what in ("data", "plane"):
            self.hist.refresh()
        if what in ("data", "plane", "range"):
            self._sync()
        elif what == "lut":
            self.hist.update()

    def _sync(self) -> None:
        if self.doc is None:
            return
        lo, hi = self.doc.display_range
        for s, v in ((self.min_spin, lo), (self.max_spin, hi)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        self.hist.update()

    def _set_range(self, lo, hi):
        if self.doc is not None:
            self.doc.set_display_range(lo, hi)

    def _spin_changed(self):
        if self.doc is not None:
            lo, hi = self.min_spin.value(), self.max_spin.value()
            if hi > lo:
                self.doc.set_display_range(lo, hi)

    def _auto(self):
        if self.doc is not None:
            self.doc.auto_range()
            self.doc.notify("range")

    def _reset(self):
        if self.doc is not None:
            self.doc.set_display_range(*self.doc.full_range())

    def _set_log(self, on):
        self.hist.log = on
        self.hist.update()

    def _set_lut(self, name):
        if self.doc is None or name not in self._luts:
            return
        lut = self._luts[name]
        self.doc.set_lut(luts.inverted(lut) if self.invert_box.isChecked() else lut)

    def select_lut(self, name: str) -> None:
        i = self.lut_combo.findText(name)
        if i >= 0:
            self.lut_combo.setCurrentIndex(i)


class DataFrameModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None):
        super().__init__()
        self._df = df if df is not None else pd.DataFrame()

    def set_df(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._df.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        v = self._df.iat[index.row(), index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(v, (float, np.floating)):
                return "" if np.isnan(v) else f"{v:.6g}"   # 4 digits turned 10000 into 1e+04
            return "" if v is None else str(v)
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(v, (int, float, np.number)):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return str(self._df.columns[section]) if orientation == Qt.Orientation.Horizontal else str(section + 1)


class ResultsPanel(QWidget):
    def __init__(self, table: ResultsTable, summary_table: ResultsTable | None = None, parent=None):
        super().__init__(parent)
        self.table = table
        self.summary_table = summary_table
        self.model = DataFrameModel(table.df)
        self.showing_summary = False
        lay = QVBoxLayout(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSortingEnabled(False)
        self.view.setAlternatingRowColors(True)
        lay.addWidget(self.view)
        self.summary_label = QLabel("Particle summary (one row per Analyze Particles run)")
        self.summary_model = DataFrameModel(summary_table.df if summary_table is not None else None)
        self.summary_view = QTableView()
        self.summary_view.setModel(self.summary_model)
        self.summary_view.setAlternatingRowColors(True)
        self.summary_view.setMaximumHeight(90)
        lay.addWidget(self.summary_label)
        lay.addWidget(self.summary_view)
        self._refresh_summary()
        if summary_table is not None:
            summary_table.listeners.append(lambda t: self._refresh_summary())
        row = QHBoxLayout()
        for text, fn in (("Save CSV…", self.save_csv), ("Clear", self._clear), ("Summary", self._toggle_summary)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.status = QLabel("")
        row.addWidget(self.status)
        lay.addLayout(row)
        table.listeners.append(lambda t: self.refresh())

    def refresh(self) -> None:
        df = self.table.summary().reset_index().rename(columns={"index": "Column"}) if self.showing_summary else self.table.df
        self.model.set_df(df)
        self.status.setText(f"{len(self.table)} rows")
        self.view.scrollToBottom()

    def _refresh_summary(self) -> None:
        df = self.summary_table.df if self.summary_table is not None else None
        self.summary_model.set_df(df if df is not None else pd.DataFrame())
        shown = df is not None and not df.empty
        self.summary_label.setVisible(shown)
        self.summary_view.setVisible(shown)

    def _clear(self) -> None:
        if self.summary_table is not None:
            self.summary_table.clear()
        self.table.clear()

    def _toggle_summary(self):
        self.showing_summary = not self.showing_summary
        self.refresh()

    def save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save results", "results.csv", "CSV (*.csv)")
        if path:
            self.table.to_csv(path)


class RoiManagerPanel(QWidget):
    measure_requested = Signal()
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.currentRowChanged.connect(self._select)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, fn in (("Add", self.add), ("Delete", self.delete), ("Measure", self.measure_requested.emit)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.show_all = QCheckBox("Show all")
        self.show_all.toggled.connect(lambda _: self.changed.emit())
        lay.addWidget(self.show_all)

    def set_doc(self, doc) -> None:
        if self.doc is not None and self._on_doc in self.doc.listeners:
            self.doc.listeners.remove(self._on_doc)
        self.doc = doc
        self.setEnabled(doc is not None)
        if doc is not None:
            doc.listeners.append(self._on_doc)
        self.refresh()

    def _on_doc(self, doc, what):
        if what == "rois":
            self.refresh()

    def refresh(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        if self.doc is not None:
            for i, r in enumerate(self.doc.rois, 1):
                x0, y0, x1, y1 = r.bounds()
                self.list.addItem(f"{i}: {r.kind} ({x0:.0f},{y0:.0f})")
        self.list.blockSignals(False)

    def add(self) -> None:
        if self.doc is not None and self.doc.roi is not None:
            self.doc.rois.append(self.doc.roi)
            self.doc.notify("rois")

    def delete(self) -> None:
        if self.doc is None:
            return
        rows = sorted({i.row() for i in self.list.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.doc.rois[r]
        self.doc.notify("rois")

    def _select(self, row: int) -> None:
        if self.doc is not None and 0 <= row < len(self.doc.rois):
            self.doc.set_roi(self.doc.rois[row])

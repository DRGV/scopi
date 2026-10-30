"""Calibration / properties dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from ..core.calibration import Calibration
from .camera_panel import load_presets, save_presets

UNITS = ["um", "mm", "nm", "cm", "pixel"]


class CalibrationDialog(QDialog):
    """Set the image scale from a known distance (e.g. a stage micrometer) or directly; save per-objective presets."""

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Scale")
        self.doc = doc
        self.presets = load_presets()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{doc.title}: {doc.width} x {doc.height} px, {doc.n_frames} frame(s), {doc.dtype}"))
        f = QFormLayout()
        self.px_len = QDoubleSpinBox()
        self.px_len.setRange(0, 1e9)
        self.px_len.setDecimals(3)
        roi = doc.roi
        if roi is not None and roi.kind == "line":
            self.px_len.setValue(roi.length())
        self.known = QDoubleSpinBox()
        self.known.setRange(0, 1e9)
        self.known.setDecimals(6)
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(UNITS)
        cal = doc.calibration
        self.unit.setCurrentText(cal.unit if cal.unit in UNITS else "um")
        self.size = QDoubleSpinBox()
        self.size.setRange(0, 1e9)
        self.size.setDecimals(8)
        self.size.setValue(cal.pixel_size if cal.calibrated else 0)
        f.addRow("Distance in pixels (draw a line first)", self.px_len)
        f.addRow("Known distance", self.known)
        f.addRow("Unit", self.unit)
        f.addRow("Pixel size (unit/px)", self.size)
        lay.addLayout(f)
        self.px_len.valueChanged.connect(self._from_known)
        self.known.valueChanged.connect(self._from_known)
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Objective name, e.g. 10x")
        save = QPushButton("Save as objective preset")
        save.clicked.connect(self._save_preset)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(load preset)")
        self.preset_combo.addItems(sorted(self.presets.presets))
        self.preset_combo.currentTextChanged.connect(self._load_preset)
        row.addWidget(self.name)
        row.addWidget(save)
        lay.addLayout(row)
        lay.addWidget(self.preset_combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _from_known(self) -> None:
        if self.px_len.value() > 0 and self.known.value() > 0:
            self.size.setValue(self.known.value() / self.px_len.value())

    def calibration(self) -> Calibration:
        unit = self.unit.currentText().strip() or "pixel"
        if self.size.value() <= 0 or unit == "pixel":
            return Calibration()
        return Calibration(self.size.value(), unit)

    def _save_preset(self) -> None:
        name = self.name.text().strip()
        cal = self.calibration()
        if not name or not cal.calibrated:
            QMessageBox.information(self, "Set Scale", "Enter an objective name and a valid scale first.")
            return
        self.presets.add(name, cal)
        save_presets(self.presets)
        self.preset_combo.addItem(name)
        QMessageBox.information(self, "Set Scale", f"Saved preset {name!r}.")

    def _load_preset(self, name: str) -> None:
        if name in self.presets.presets:
            c = self.presets.get(name)
            self.size.setValue(c.pixel_size)
            self.unit.setCurrentText(c.unit)

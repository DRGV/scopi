"""Parameter dialog generated from an op's declarative ``view``."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..ops.base import Op, Param


class _Field:
    """A widget plus getter, built from a Param."""

    def __init__(self, p: Param, on_change):
        self.p = p
        k = p.kind
        if k == "int":
            self.w = QWidget()
            lay = QHBoxLayout(self.w)
            lay.setContentsMargins(0, 0, 0, 0)
            self.spin = QSpinBox()
            lo, hi = p.range
            self.spin.setRange(int(max(lo, -2**31 + 1)), int(min(hi, 2**31 - 1)))
            self.spin.setValue(int(p.default))
            if p.unit:
                self.spin.setSuffix(f" {p.unit}")
            self.slider = QSlider(Qt.Orientation.Horizontal)
            self.slider.setRange(self.spin.minimum(), min(self.spin.maximum(), self.spin.minimum() + 1000)) if hi - lo > 1000 else self.slider.setRange(self.spin.minimum(), self.spin.maximum())
            self.slider.setValue(self.spin.value())
            self.slider.valueChanged.connect(self.spin.setValue)
            self.spin.valueChanged.connect(lambda v: (self.slider.blockSignals(True), self.slider.setValue(v), self.slider.blockSignals(False)))
            self.spin.valueChanged.connect(lambda _: on_change())
            lay.addWidget(self.slider, 1)
            lay.addWidget(self.spin)
            self.get = lambda: self.spin.value()
        elif k == "float":
            lo, hi = p.range
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(p.digits)
            self.spin.setRange(lo, hi)
            self.spin.setSingleStep(10 ** -p.digits if hi - lo < 100 else 1.0)
            self.spin.setValue(float(p.default))
            if p.unit:
                self.spin.setSuffix(f" {p.unit}")
            self.spin.valueChanged.connect(lambda _: on_change())
            self.get = lambda: self.spin.value()
            if hi - lo <= 1000:   # a bounded range gets a slider too, handy with the live preview
                steps = max(min(int(round((hi - lo) * 10 ** p.digits)), 1000), 1)
                self.slider = QSlider(Qt.Orientation.Horizontal)
                self.slider.setRange(0, steps)
                to_pos = lambda v: int(round((v - lo) / (hi - lo) * steps))
                self.slider.setValue(to_pos(self.spin.value()))
                self.slider.valueChanged.connect(lambda i: self.spin.setValue(round(lo + (hi - lo) * i / steps, p.digits)))
                self.spin.valueChanged.connect(lambda v: (self.slider.blockSignals(True), self.slider.setValue(to_pos(v)), self.slider.blockSignals(False)))
                self.w = QWidget()
                lay = QHBoxLayout(self.w)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(self.slider, 1)
                lay.addWidget(self.spin)
            else:
                self.w = self.spin
        elif k == "choice":
            self.w = QComboBox()
            self.w.addItems([str(c) for c in p.choices])
            self.w.setCurrentIndex(p.choices.index(p.default))
            self.w.currentIndexChanged.connect(lambda _: on_change())
            self.get = lambda: p.choices[self.w.currentIndex()]
        elif k == "bool":
            self.w = QCheckBox()
            self.w.setChecked(bool(p.default))
            self.w.toggled.connect(lambda _: on_change())
            self.get = lambda: self.w.isChecked()
        else:
            self.w = QLineEdit(str(p.default))
            self.w.textChanged.connect(lambda _: on_change())
            self.get = lambda: self.w.text()


class ParaDialog(QDialog):
    """Emits ``preview_requested(para)`` (debounced) while the Preview box is checked."""

    preview_requested = Signal(dict)
    preview_cleared = Signal()

    def __init__(self, op: Op, view: list[Param], *, can_preview: bool, can_stack: bool, initial: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(op.title)
        self.op = op
        self._fields: dict[str, _Field] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._emit_preview)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        for p in view:
            if initial and p.key in initial:
                p = Param(**{**p.__dict__, "default": initial[p.key]})
            f = _Field(p, self._changed)
            self._fields[p.key] = f
            if p.kind == "bool":
                form.addRow(f.w, QLabel(p.label))
            else:
                form.addRow(p.label, f.w)
        lay.addLayout(form)
        self.preview_box = QCheckBox("Preview")
        self.preview_box.setVisible(can_preview)
        self.preview_box.toggled.connect(self._preview_toggled)
        self.stack_box = QCheckBox("Process entire stack")
        self.stack_box.setVisible(can_stack)
        lay.addWidget(self.preview_box)
        lay.addWidget(self.stack_box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.finished.connect(lambda _: self.preview_cleared.emit())

    def para(self) -> dict:
        return {k: f.get() for k, f in self._fields.items()}

    @property
    def whole_stack(self) -> bool:
        return self.stack_box.isVisible() and self.stack_box.isChecked()

    def _changed(self) -> None:
        # Throttle, don't debounce: restarting the timer on every change meant the picture only updated once a dragged
        # slider paused (round 4: "the slider produces no change in preview mode"). Now it follows at about 4 updates a second.
        if self.preview_box.isChecked() and not self._timer.isActive():
            self._timer.start()

    def _preview_toggled(self, on: bool) -> None:
        if on:
            self._emit_preview()
        else:
            self.preview_cleared.emit()

    def _emit_preview(self) -> None:
        if self.preview_box.isChecked():
            self.preview_requested.emit(self.para())

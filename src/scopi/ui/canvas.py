# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Image canvas: QGraphicsView whose background is the rendered viewport.

The scene is in image-pixel coordinates (pixel i spans [i, i+1)). Pixels are produced by the
Qt-free DisplayPipeline for the visible viewport only; ROI/measure overlays are vector
QGraphicsItems with cosmetic pens so they stay crisp at any zoom.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QGestureEvent, QGraphicsItem, QGraphicsScene, QGraphicsView, QPinchGesture

from ..core.image import ImageDoc
from ..core.roi import Angle, Ellipse, Line, Point, Polygon, Rect, Roi
from . import render
from .tools import TOOLS, PointerTool, Tool, ToolContext

MIN_ZOOM, MAX_ZOOM = 0.01, 64.0
ROI_COLOR = QColor(255, 235, 0)
OTHER_ROI_COLOR = QColor(0, 220, 255)
PREVIEW_COLOR = QColor(255, 255, 255)


def roi_path(roi: Roi) -> QPainterPath:
    p = QPainterPath()
    if isinstance(roi, Rect):
        p.addRect(QRectF(roi.x, roi.y, roi.w, roi.h))
    elif isinstance(roi, Ellipse):
        p.addEllipse(QRectF(roi.x, roi.y, roi.w, roi.h))
    elif isinstance(roi, Polygon):
        pts = roi.points
        if pts:
            p.moveTo(*pts[0])
            for q in pts[1:]:
                p.lineTo(*q)
            if len(pts) >= 3:
                p.closeSubpath()
    elif isinstance(roi, Line):
        p.moveTo(roi.x0, roi.y0)
        p.lineTo(roi.x1, roi.y1)
    elif isinstance(roi, Angle):
        p.moveTo(*roi.p1)
        p.lineTo(*roi.vertex)
        p.lineTo(*roi.p2)
    return p


class RoiItem(QGraphicsItem):
    """Vector ROI. Cosmetic pen => constant on-screen width; handles are sized in screen pixels."""

    def __init__(self, roi: Roi, color: QColor, *, dashed=False, handles=None, label: str | None = None):
        super().__init__()
        self.roi = roi
        self.color = color
        self.dashed = dashed
        self.handle_pts = handles or []
        self.label = label
        self.setZValue(10)

    def boundingRect(self) -> QRectF:
        x0, y0, x1, y1 = self.roi.bounds()
        m = 40.0  # generous: handles and labels are in screen pixels
        return QRectF(x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m)

    def paint(self, painter: QPainter, option, widget=None):
        pen = QPen(self.color, 1.5)
        pen.setCosmetic(True)
        if self.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if isinstance(self.roi, Point):
            lod = painter.worldTransform().m11() or 1.0
            r = 6.0 / lod
            painter.drawLine(QPointF(self.roi.x - r, self.roi.y), QPointF(self.roi.x + r, self.roi.y))
            painter.drawLine(QPointF(self.roi.x, self.roi.y - r), QPointF(self.roi.x, self.roi.y + r))
        else:
            painter.drawPath(roi_path(self.roi))
        if self.handle_pts:
            lod = painter.worldTransform().m11() or 1.0
            hs = 4.0 / lod
            painter.setBrush(QColor(255, 255, 255))
            for x, y in self.handle_pts:
                painter.drawRect(QRectF(x - hs, y - hs, 2 * hs, 2 * hs))
        if self.label:
            x0, y0, _, _ = self.roi.bounds()
            painter.save()
            lod = painter.worldTransform().m11() or 1.0
            painter.translate(x0, y0)
            painter.scale(1 / lod, 1 / lod)
            painter.setPen(self.color)
            painter.drawText(QPointF(3, -3), self.label)
            painter.restore()


class ImageCanvas(QGraphicsView):
    cursor_info = Signal(float, float, object)   # image x, y, pixel value (or None)
    zoom_changed = Signal(float)                 # scale factor
    view_changed = Signal()

    def __init__(self, doc: ImageDoc, pipeline: render.DisplayPipeline | None = None, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.pipeline = pipeline or render.DisplayPipeline()
        self.show_scale_bar = True
        self.show_all_rois = False
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.grabGesture(Qt.GestureType.PinchGesture)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._frame = None
        self._scale = 1.0
        self._panning = False
        self._pan_last = QPointF()
        self._space = False
        self._override: tuple[np.ndarray, tuple[float, float]] | None = None
        self._override_region: tuple[int, int, int, int] | None = None   # (x0, y0, x1, y1) the preview was computed on
        self._override_token = 0
        self._items: list[QGraphicsItem] = []
        self._preview_item: QGraphicsItem | None = None
        self._pinch_base = 1.0
        self._fitted = False
        self._size = (doc.width, doc.height)

        ctx = ToolContext(commit=self._commit_roi, get_roi=lambda: self.doc.roi, add_marker=self._add_marker,
                          tolerance=lambda: 8.0 / max(self._scale, 1e-6))
        self._ctx = ctx
        self.tool: Tool = PointerTool(ctx)
        self._set_scene_rect()
        doc.listeners.append(self._on_doc)
        self.rebuild_overlays()

    # ------------------------------------------------------------ doc plumbing
    def close_view(self) -> None:
        if self._on_doc in self.doc.listeners:
            self.doc.listeners.remove(self._on_doc)

    def _set_scene_rect(self) -> None:
        w, h = self.doc.width, self.doc.height
        self._scene.setSceneRect(QRectF(-w, -h, 3 * w, 3 * h))

    def _on_doc(self, doc, what: str) -> None:
        if what == "data" and (doc.width, doc.height) != self._size:
            self._size = (doc.width, doc.height)
            self._set_scene_rect()
            self.fit()
        if what in ("roi", "rois", "data"):
            self.rebuild_overlays()
        self.viewport().update()

    def _plane_and_range(self):
        if self._override is not None:
            return self._override[0], self._override[1], ("ov", self._override_token)
        d = self.doc
        return d.frame(), d.display_range, (id(d), d.version, d.plane)

    # ------------------------------------------------------------ preview override
    def set_preview(self, plane: np.ndarray | None, display_range: tuple[float, float] | None = None,
                    region: tuple[int, int, int, int] | None = None) -> None:
        """Show ``plane`` instead of the document's current plane (live preview). None restores.

        ``region`` (x0, y0, x1, y1) is the part of the plane the preview was computed on; when it leaves some of the visible
        picture out, a frame is drawn around it (see ``preview_box``)."""
        if plane is None:
            self._override = None
            self._override_region = None
        else:
            self._override = (plane, display_range or self.doc.display_range)
            self._override_region = region
            self._override_token += 1
        self.viewport().update()

    def preview_box(self) -> QRectF | None:
        """The previewed area in image coordinates if it leaves out more than 5 % of the picture on screen, else None.

        A preview only works on a crop of at most PREVIEW_MAX pixels a side, so on a big picture at low zoom most of it stays
        unfiltered until OK; test5 B6 asked for that area to be shown. A 1228 x 922 picture at Fit misses only a strip of 13 px
        at each side, and a frame around nearly everything would just be clutter."""
        if self._override is None or self._override_region is None:
            return None
        x0, y0, x1, y1 = self._override_region
        box = QRectF(x0, y0, x1 - x0, y1 - y0)
        seen = self.view_rect().intersected(QRectF(0, 0, self.doc.width, self.doc.height))
        covered = box.intersected(seen)
        if seen.isEmpty() or covered.width() * covered.height() >= 0.95 * seen.width() * seen.height():
            return None
        return box

    # ------------------------------------------------------------ view transform
    @property
    def scale_factor(self) -> float:
        return self._scale

    def view_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def set_scale(self, s: float, anchor: QPointF | None = None) -> None:
        s = float(np.clip(s, MIN_ZOOM, MAX_ZOOM))
        if anchor is None:
            anchor = self.view_rect().center()
        vp_pt = self.mapFromScene(anchor)
        self._scale = s
        self.setTransform(QTransform.fromScale(s, s))
        # keep the anchored scene point under the same viewport point
        delta = self.mapFromScene(anchor) - vp_pt
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        self.zoom_changed.emit(s)
        self.view_changed.emit()
        self.viewport().update()

    def fit(self) -> None:
        vw, vh = max(self.viewport().width(), 1), max(self.viewport().height(), 1)
        s = min(vw / self.doc.width, vh / self.doc.height)
        s = min(s, 1.0) if min(self.doc.width, self.doc.height) > 64 else s
        self._scale = float(np.clip(s, MIN_ZOOM, MAX_ZOOM))
        self.setTransform(QTransform.fromScale(self._scale, self._scale))
        self.centerOn(self.doc.width / 2, self.doc.height / 2)
        self._fitted = True
        self.zoom_changed.emit(self._scale)
        self.view_changed.emit()
        self.viewport().update()

    def zoom_100(self) -> None:
        self.set_scale(1.0)

    def zoom_in(self) -> None:
        self.set_scale(self._scale * 1.25)

    def zoom_out(self) -> None:
        self.set_scale(self._scale / 1.25)

    def zoom_to_rect(self, x0, y0, x1, y1) -> None:
        w = max(x1 - x0, 1)
        h = max(y1 - y0, 1)
        s = min(self.viewport().width() / w, self.viewport().height() / h)
        self.set_scale(s, QPointF((x0 + x1) / 2, (y0 + y1) / 2))
        self.centerOn((x0 + x1) / 2, (y0 + y1) / 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._fitted:
            self.fit()
        self.view_changed.emit()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._fitted:
            self.fit()

    # ------------------------------------------------------------ painting
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        vp = self.viewport()
        dpr = vp.devicePixelRatioF()
        w, h = max(int(vp.width() * dpr), 1), max(int(vp.height() * dpr), 1)
        v = self.view_rect()
        plane, rng, token = self._plane_and_range()
        labels = self.doc.labels if self._override is None else None
        arr = self.pipeline.render(plane, rng, self.doc.lut, (v.left(), v.top(), v.right(), v.bottom()), (w, h),
                                   token=token, labels=labels)
        self._frame = arr  # keep the buffer alive for the QImage
        img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888)
        img.setDevicePixelRatio(dpr)
        painter.save()
        painter.resetTransform()
        painter.drawImage(0, 0, img)
        painter.restore()

    def _draw_preview_box(self, painter: QPainter) -> None:
        box = self.preview_box()
        if box is None:
            return
        r = self.mapFromScene(box).boundingRect().adjusted(0, 0, -1, -1)
        painter.save()
        painter.resetTransform()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 220), 3))                        # dark under light: visible on any picture
        painter.drawRect(r)
        pen = QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(r)
        f = QFont(painter.font())
        f.setBold(True)
        painter.setFont(f)
        for col, off in ((QColor(0, 0, 0), 1), (QColor(255, 255, 255), 0)):
            painter.setPen(col)
            painter.drawText(QPointF(r.left() + 6 + off, r.top() + 16 + off), "Preview area")
        painter.restore()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        self._draw_preview_box(painter)
        cal = self.doc.calibration
        if not (self.show_scale_bar and cal.calibrated):
            return
        vp = self.viewport()
        width_img = vp.width() / self._scale
        length_units, length_px_img = cal.scale_bar_length(int(width_img), 0.2)
        bar_px = length_px_img * self._scale
        painter.save()
        painter.resetTransform()
        x1, y = vp.width() - 20, vp.height() - 24
        x0 = x1 - bar_px
        painter.setPen(QPen(QColor(0, 0, 0, 200), 6))
        painter.drawLine(QPointF(x0, y), QPointF(x1, y))
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawLine(QPointF(x0, y), QPointF(x1, y))
        f = QFont(painter.font())
        f.setBold(True)
        painter.setFont(f)
        text = f"{length_units:g} {cal.unit}"
        for col, off in ((QColor(0, 0, 0), 1), (QColor(255, 255, 255), 0)):
            painter.setPen(col)
            painter.drawText(QPointF(x0 + off, y - 8 + off), text)
        painter.restore()

    # ------------------------------------------------------------ overlays
    def rebuild_overlays(self) -> None:
        for it in self._items:
            self._scene.removeItem(it)
        self._items.clear()
        if self.show_all_rois:
            for i, r in enumerate(self.doc.rois, 1):
                it = RoiItem(r, OTHER_ROI_COLOR, label=str(i))
                self._scene.addItem(it)
                self._items.append(it)
        r = self.doc.roi
        if r is not None:
            handles = list(PointerTool(self._ctx).handles(r).values()) if isinstance(self.tool, PointerTool) else []
            it = RoiItem(r, ROI_COLOR, handles=handles)
            self._scene.addItem(it)
            self._items.append(it)
        self._update_preview_item()

    def _update_preview_item(self) -> None:
        if self._preview_item is not None:
            self._scene.removeItem(self._preview_item)
            self._preview_item = None
        if self.tool.preview is not None:
            self._preview_item = RoiItem(self.tool.preview, PREVIEW_COLOR, dashed=True)
            self._scene.addItem(self._preview_item)

    def set_tool(self, name: str) -> None:
        self.tool.cancel()
        self.tool = TOOLS[name](self._ctx)
        self.setCursor({"cross": Qt.CursorShape.CrossCursor, "arrow": Qt.CursorShape.ArrowCursor}.get(self.tool.cursor, Qt.CursorShape.CrossCursor))
        self.rebuild_overlays()

    def set_hand_tool(self) -> None:
        self.tool.cancel()
        self.tool = _HandTool(self._ctx)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.rebuild_overlays()

    def _commit_roi(self, roi: Roi | None) -> None:
        self.doc.set_roi(roi)

    def _add_marker(self, p: Point) -> None:
        self.doc.rois.append(p)
        self.doc.notify("rois")

    # ------------------------------------------------------------ input
    def _scene_pt(self, e) -> QPointF:
        return self.mapToScene(e.position().toPoint())

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MouseButton.MiddleButton or (e.button() == Qt.MouseButton.LeftButton and (self._space or self.tool.pan)):
            self._panning = True
            self._pan_last = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            p = self._scene_pt(e)
            self.tool.press(p.x(), p.y(), bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self._after_tool()

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.position() - self._pan_last
            self._pan_last = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            self.view_changed.emit()
            self.viewport().update()
            return
        p = self._scene_pt(e)
        self.cursor_info.emit(p.x(), p.y(), render.value_at(self.doc.frame(), p.x(), p.y()))
        if e.buttons() & Qt.MouseButton.LeftButton or self.tool.busy:
            self.tool.move(p.x(), p.y(), bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self._after_tool()

    def mouseReleaseEvent(self, e):
        if self._panning and e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._panning = False
            self.setCursor(Qt.CursorShape.OpenHandCursor if isinstance(self.tool, _HandTool) else Qt.CursorShape.CrossCursor)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            p = self._scene_pt(e)
            self.tool.release(p.x(), p.y(), bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self._after_tool()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            p = self._scene_pt(e)
            self.tool.double_click(p.x(), p.y())
            self._after_tool()

    def _after_tool(self) -> None:
        self._update_preview_item()
        self.viewport().update()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy == 0:
            return
        self.set_scale(self._scale * (1.25 ** (dy / 120.0)), self.mapToScene(e.position().toPoint()))

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Space:
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        names = {Qt.Key.Key_Escape: "escape", Qt.Key.Key_Return: "return", Qt.Key.Key_Enter: "enter", Qt.Key.Key_Backspace: "backspace"}
        if k in names:
            self.tool.key(names[k])
            self._after_tool()
            return
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
        elif k == Qt.Key.Key_Minus:
            self.zoom_out()
        elif k in (Qt.Key.Key_Delete,):
            self.doc.set_roi(None)
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            super().keyReleaseEvent(e)

    def event(self, e):
        if isinstance(e, QGestureEvent):
            g = e.gesture(Qt.GestureType.PinchGesture)
            if isinstance(g, QPinchGesture):
                if g.state() == Qt.GestureState.GestureStarted:
                    self._pinch_base = self._scale
                self.set_scale(self._pinch_base * g.totalScaleFactor(), self.mapToScene(self.mapFromGlobal(g.hotSpot().toPoint())))
                return True
        return super().event(e)


class _HandTool(Tool):
    name = "hand"
    pan = True
    cursor = "arrow"

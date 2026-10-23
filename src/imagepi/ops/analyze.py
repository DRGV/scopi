"""Measurement and analysis (Analyze menu)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from skimage import measure

from .base import Bool, Choice, Float, Int, Simple, register

CAT = "Analyze"


def roi_stats(doc, roi=None) -> dict:
    """Measurements of ``roi`` (default: the whole current plane) on the current plane."""
    cal = doc.calibration
    plane = doc.gray_frame()
    out: dict = {"Image": doc.title, "Frame": doc.plane + 1}
    if roi is not None and not roi.is_area:
        kind = roi.kind
        out["Type"] = kind
        if kind == "line":
            out["Length"] = cal.to_units(roi.length())
            out["Angle"] = roi.angle()
            prof = roi.profile(plane)
            out.update(Mean=float(prof.mean()), Min=float(prof.min()), Max=float(prof.max()), StdDev=float(prof.std()))
        elif kind == "angle":
            out["Angle"] = roi.angle()
        elif kind == "point":
            x, y = int(round(roi.x)), int(round(roi.y))
            out.update(X=cal.to_units(roi.x), Y=cal.to_units(roi.y))
            if 0 <= y < plane.shape[0] and 0 <= x < plane.shape[1]:
                out["Value"] = float(plane[y, x])
        return out
    mask = roi.mask(plane.shape) if roi is not None else np.ones(plane.shape, bool)
    vals = plane[mask]
    if vals.size == 0:
        raise ValueError("ROI is empty or outside the image")
    ys, xs = np.nonzero(mask)
    out["Type"] = roi.kind if roi is not None else "image"
    out["Area"] = cal.area_to_units(float(mask.sum()))
    out.update(Mean=float(vals.mean()), Min=float(vals.min()), Max=float(vals.max()), StdDev=float(vals.std()),
               X=cal.to_units(float(xs.mean() + 0.5)), Y=cal.to_units(float(ys.mean() + 0.5)))
    if roi is not None:
        out["Perim."] = cal.to_units(roi.perimeter())
    out["Unit"] = cal.unit
    return out


@register
class Measure(Simple):
    title, category = "Measure", CAT

    def run(self, doc, para, session):
        row = roi_stats(doc, doc.roi)
        session.results.add_row(**row)
        return row


@register
class MeasureAllRois(Simple):
    title, category = "Measure All ROIs", CAT

    def run(self, doc, para, session):
        rows = [roi_stats(doc, r) for r in doc.rois]
        session.results.add_rows(rows)
        return rows


@register
class ClearResults(Simple):
    title, category = "Clear Results", CAT

    def run(self, doc, para, session):
        session.results.clear()
        session.summary.clear()


@register
class CountPoints(Simple):
    """Marker counting: reports the point ROIs in the ROI manager."""

    title, category = "Count Markers", CAT

    def run(self, doc, para, session):
        pts = [r for r in doc.rois if r.kind == "point"]
        cal = doc.calibration
        for i, p in enumerate(pts, 1):
            session.results.add_row(Image=doc.title, Marker=i, X=cal.to_units(p.x), Y=cal.to_units(p.y))
        return len(pts)


@register
class AreaFraction(Simple):
    title, category = "Area Fraction", CAT

    def check(self, doc):
        return None

    def run(self, doc, para, session):
        mask = doc.roi_mask()
        plane = doc.gray_frame() > 0
        region = np.ones_like(plane) if mask is None else mask
        n = int(region.sum())
        frac = 100.0 * float((plane & region).sum()) / n if n else 0.0
        row = {"Image": doc.title, "Area": doc.calibration.area_to_units(n), "%Area": frac, "Unit": doc.calibration.unit}
        session.results.add_row(**row)
        return row


# ------------------------------------------------------------------ particles
def particle_table(labels: np.ndarray, intensity: np.ndarray, cal) -> pd.DataFrame:
    """Per-object geometry and intensity from a label image (regionprops)."""
    rows = []
    for rp in measure.regionprops(labels, intensity_image=intensity):
        perim = rp.perimeter
        circ = min(4 * np.pi * rp.area / perim ** 2, 1.0) if perim > 0 else 0.0
        try:
            feret = rp.feret_diameter_max
        except Exception:
            feret = 0.0
        cy, cx = rp.centroid
        rows.append({
            "Label": rp.label,
            "Area": cal.area_to_units(rp.area),
            "X": cal.to_units(cx + 0.5), "Y": cal.to_units(cy + 0.5),
            "Perim.": cal.to_units(perim),
            "Major": cal.to_units(rp.axis_major_length), "Minor": cal.to_units(rp.axis_minor_length),
            "Angle": float(np.degrees(rp.orientation)),
            "Circ.": circ,
            "Feret": cal.to_units(feret),
            "Solidity": float(rp.solidity),
            "Mean": float(rp.intensity_mean), "Min": float(rp.intensity_min), "Max": float(rp.intensity_max),
        })
    return pd.DataFrame(rows)


def analyze_particles(mask: np.ndarray, intensity: np.ndarray, cal, *, min_size=0.0, max_size=float("inf"),
                      min_circ=0.0, max_circ=1.0, exclude_edges=False, connectivity=2):
    """Returns (table, labels). Size limits are in calibrated units^2."""
    labels = measure.label(mask > 0, connectivity=connectivity)
    if exclude_edges:
        edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        labels[np.isin(labels, edge[edge > 0])] = 0
    df = particle_table(labels, intensity, cal)
    if df.empty:
        return df, labels
    keep = (df["Area"] >= min_size) & (df["Area"] <= max_size) & (df["Circ."] >= min_circ) & (df["Circ."] <= max_circ)
    drop = df.loc[~keep, "Label"].to_numpy()
    if drop.size:
        labels[np.isin(labels, drop)] = 0
    df = df[keep].reset_index(drop=True)
    return df, labels


@register
class AnalyzeParticles(Simple):
    title, category = "Analyze Particles", CAT
    view = [
        Float("min_size", "Minimum size", (0.0, 1e12), 0.0, 1),
        Float("max_size", "Maximum size", (0.0, 1e12), 1e12, 1),
        Float("min_circ", "Minimum circularity", (0.0, 1.0), 0.0, 2),
        Float("max_circ", "Maximum circularity", (0.0, 1.0), 1.0, 2),
        Bool("exclude_edges", "Exclude on edges", False),
        Bool("show_overlay", "Show outlines", True),
        Bool("summary", "Add summary row", True),
    ]

    def run(self, doc, para, session):
        gray = doc.gray_frame()
        mask = doc.roi_mask()
        binary = gray > 0
        if mask is not None:
            binary = binary & mask
        df, labels = analyze_particles(
            binary, gray, doc.calibration, min_size=para["min_size"], max_size=para["max_size"],
            min_circ=para["min_circ"], max_circ=para["max_circ"], exclude_edges=para["exclude_edges"])
        doc.labels = labels if para["show_overlay"] else None
        if not df.empty:
            df.insert(0, "Image", doc.title)
            session.results.add_rows(df.to_dict("records"))
        if para["summary"]:
            region_px = int(mask.sum()) if mask is not None else gray.size
            region_area = doc.calibration.area_to_units(region_px)
            total = float(df["Area"].sum()) if not df.empty else 0.0
            summary = {"Image": doc.title, "Count": len(df), "Total Area": total,
                       "Average Size": total / len(df) if len(df) else 0.0,
                       "%Area": 100.0 * total / region_area if region_area else 0.0,
                       "Density": len(df) / region_area if region_area else 0.0, "Unit": doc.calibration.unit}
            doc.metadata["particle_summary"] = summary
            session.summary.add_row(**summary)
        doc.notify("labels")
        return df


@register
class RegionProps(Simple):
    """Geometry of a label image (e.g. after Label / segmentation ops), measured on the original intensities."""

    title, category = "Measure Labels", CAT

    def run(self, doc, para, session):
        if doc.labels is None:
            raise ValueError("No label overlay; run Analyze Particles or a segmentation op first")
        df = particle_table(doc.labels, doc.gray_frame(), doc.calibration)
        df.insert(0, "Image", doc.title)
        session.results.add_rows(df.to_dict("records"))
        return df

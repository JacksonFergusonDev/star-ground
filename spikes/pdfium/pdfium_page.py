# ruff: noqa
# mypy: ignore-errors
"""Spike: a pdfplumber Page whose objects come from pdfium instead of pdfminer."""

import ctypes, math
import pypdfium2 as pdfium
import pypdfium2.raw as C
from star_ground.bom_lib.pdf import guard

_rect = C.FS_RECTF()
_mat = C.FS_MATRIX()
_ox = ctypes.c_double()
_oy = ctypes.c_double()
_buf = ctypes.create_string_buffer(256)
_flags = ctypes.c_int()
_x = ctypes.c_float()
_y = ctypes.c_float()
_fill = ctypes.c_int()
_stroke = ctypes.c_int()
_r = ctypes.c_uint()
_g = ctypes.c_uint()
_b = ctypes.c_uint()
_a = ctypes.c_uint()
_lw = ctypes.c_float()
DOCS = {}  # filepath -> PdfDocument (set by the harness)
CURRENT = {"path": None}


def _color(getter, obj):
    if getter(obj, _r, _g, _b, _a):
        return (_r.value / 255, _g.value / 255, _b.value / 255)
    return None


def _chars(pg, tp, H, mb_top, number):
    n = tp.count_chars()
    s = tp.get_text_range()
    out = []
    raw = tp.raw
    gen = C.FPDFText_IsGenerated
    fontcache = {}
    for k in range(n):
        if gen(raw, k):
            continue
        ch = s[k] if k < len(s) else ""
        if ch in ("\r", "\n", "\x00", "￾"):
            continue
        C.FPDFText_GetLooseCharBox(raw, k, _rect)
        l, b, r, t = _rect.left, _rect.bottom, _rect.right, _rect.top
        C.FPDFText_GetMatrix(raw, k, _mat)
        size = C.FPDFText_GetFontSize(raw, k) * math.sqrt(
            abs(_mat.a * _mat.d - _mat.b * _mat.c)
        )
        C.FPDFText_GetCharOrigin(raw, k, _ox, _oy)
        if out and out[-1]["_box"] == (l, b, r, t):
            out[-1]["text"] += (
                ch  # a ligature: pdfium gives each letter the glyph's box
            )
            continue
        nlen = C.FPDFText_GetFontInfo(raw, k, _buf, 256, _flags)
        name = _buf.raw[: max(nlen - 1, 0)].decode("latin-1")
        a, bb, c, d = _mat.a, _mat.b, _mat.c, _mat.d
        upright = (
            (0 < a * d) and (bb == 0 or abs(bb) < 1e-6) and (c == 0 or abs(c) < 1e-6)
        )
        ys0 = b
        ys1 = b + size if upright else t
        top = (H - ys1) + mb_top
        bottom = (H - ys0) + mb_top
        out.append(
            {
                "object_type": "char",
                "page_number": number,
                "text": ch,
                "fontname": name,
                "size": size,
                "adv": r - l,
                "upright": upright,
                "height": size,
                "width": r - l,
                "x0": l,
                "x1": r,
                "y0": ys0,
                "y1": ys1,
                "top": top,
                "bottom": bottom,
                "matrix": (a, bb, c, d, _ox.value, _oy.value),
                "stroking_color": (0,),
                "non_stroking_color": (0,),
                "mcid": None,
                "tag": None,
                "_box": (l, b, r, t),
            }
        )
    return out


def _paths(pg, H, mb_top, number, doctop0):
    out = []
    for obj in pg.get_objects(filter=[C.FPDF_PAGEOBJ_PATH], max_depth=0):
        h = obj.raw
        C.FPDFPageObj_GetMatrix(h, _mat)
        m = (_mat.a, _mat.b, _mat.c, _mat.d, _mat.e, _mat.f)
        n = C.FPDFPath_CountSegments(h)
        pts = []
        kinds = []
        for i in range(n):
            seg = C.FPDFPath_GetPathSegment(h, i)
            C.FPDFPathSegment_GetPoint(seg, _x, _y)
            px = m[0] * _x.value + m[2] * _y.value + m[4]
            py = m[1] * _x.value + m[3] * _y.value + m[5]
            pts.append((px, py))
            kinds.append(C.FPDFPathSegment_GetType(seg))
        if not pts:
            continue
        C.FPDFPath_GetDrawMode(h, _fill, _stroke)
        C.FPDFPageObj_GetStrokeWidth(h, _lw)
        sc = _color(C.FPDFPageObj_GetStrokeColor, h)
        nc = _color(C.FPDFPageObj_GetFillColor, h)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        top = (H - y1) + mb_top
        bottom = (H - y0) + mb_top
        ptsd = [(px, (H - py) + mb_top) for px, py in pts]
        bez = C.FPDF_SEGMENT_BEZIERTO in kinds
        npts = len(pts)
        axis = lambda p, q: abs(p[0] - q[0]) < 1e-6 or abs(p[1] - q[1]) < 1e-6
        if not bez and npts == 2:
            kind = "line"
        elif (
            not bez
            and npts in (4, 5)
            and all(axis(pts[i], pts[(i + 1) % npts]) for i in range(npts))
            and (
                npts == 4
                or abs(pts[0][0] - pts[-1][0]) < 1e-6
                and abs(pts[0][1] - pts[-1][1]) < 1e-6
            )
            and len({round(v, 3) for v in xs}) == 2
            and len({round(v, 3) for v in ys}) == 2
        ):
            kind = "rect"
        else:
            kind = "curve"
        d = {
            "object_type": kind,
            "page_number": number,
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y1,
            "top": top,
            "bottom": bottom,
            "doctop": doctop0 + top,
            "width": x1 - x0,
            "height": y1 - y0,
            "pts": ptsd,
            "linewidth": _lw.value,
            "stroke": bool(_stroke.value),
            "fill": bool(_fill.value),
            "stroking_color": sc,
            "non_stroking_color": nc,
            "evenodd": False,
            "path": [("m" if kinds[0] == C.FPDF_SEGMENT_MOVETO else "l", ptsd[0])],
            "dash": ([], 0),
            "mcid": None,
            "tag": None,
        }
        out.append(d)
    return out


def _images(pg, H, mb_top, number, doctop0):
    out = []
    for obj in pg.get_objects(filter=[C.FPDF_PAGEOBJ_IMAGE], max_depth=15):
        l, b, r, t = obj.get_bounds()
        top = (H - t) + mb_top
        bottom = (H - b) + mb_top
        out.append(
            {
                "object_type": "image",
                "page_number": number,
                "x0": l,
                "x1": r,
                "y0": b,
                "y1": t,
                "top": top,
                "bottom": bottom,
                "doctop": doctop0 + top,
                "width": r - l,
                "height": t - b,
                "srcsize": (0, 0),
                "name": "im",
                "colorspace": None,
                "bits": 8,
                "stream": None,
                "imagemask": None,
                "mcid": None,
                "tag": None,
            }
        )
    return out


def parse_objects(self):
    key = id(self.pdf)
    if key not in DOCS:
        st = self.pdf.stream
        st.seek(0)
        DOCS[key] = (pdfium.PdfDocument(st.read()), self.pdf)
    doc = DOCS[key][0]
    pg = doc[self.page_number - 1]
    H = self.height
    mb_top = self.mediabox[1]
    tp = pg.get_textpage()
    objects = {"char": _chars(pg, tp, H, mb_top, self.page_number)}
    tp.close()
    for o in _paths(pg, H, mb_top, self.page_number, self.initial_doctop):
        objects.setdefault(o["object_type"], []).append(o)
    objects["image"] = _images(pg, H, mb_top, self.page_number, self.initial_doctop)
    for ch in objects["char"]:
        ch["doctop"] = self.initial_doctop + ch["top"]
    if len(objects["char"]) > guard.MAX_PAGE_CHARS:
        raise guard.CrowdedPageError(self.page_number)
    return objects


def install():
    guard.GuardedPage.parse_objects = parse_objects
    guard.parse_page = lambda *a, **k: None
    import star_ground.bom_lib.pdf.document as d

    d.parse_page = lambda *a, **k: None
    import star_ground.bom_lib as b

    return d.parse_pdf, d.parse_pdf

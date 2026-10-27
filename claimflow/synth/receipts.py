"""Render fake clinic receipts as images, then degrade them like real phone photos.

Three layouts make sure the extractor can't rely on fields always being in the
same place. Degradation levels:
    mild  - slight tilt, light noise, normal JPEG (a decent phone photo)
    heavy - visible tilt, blur, faded contrast, shadow, low-quality JPEG
"""

from __future__ import annotations

import io
import os
import random
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

LAYOUTS = ("classic", "pos", "invoice")

# ---------------------------------------------------------------------------
# Fonts: use common system fonts if present, fall back to Pillow's built-in font.
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = {
    "sans": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf"],
    "sans_bold": ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"],
    "serif": ["DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "Times New Roman.ttf", "times.ttf"],
    "serif_bold": ["DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf",
                   "Times New Roman Bold.ttf", "timesbd.ttf"],
    "mono": ["DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "Courier New.ttf", "cour.ttf"],
}

_FONT_DIRS = [
    "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
    "/Library/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
    "C:/Windows/Fonts",
]


@lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for root_dir in _FONT_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for root, _dirs, files in os.walk(root_dir):
            for name in files:
                index.setdefault(name.lower(), os.path.join(root, name))
    return index


@lru_cache(maxsize=None)
def font(family: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    index = _font_index()
    for candidate in _FONT_CANDIDATES[family]:
        path = index.get(candidate.lower())
        if path:
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------------------
# Formatting helpers: receipts write dates and money in different ways.
# ---------------------------------------------------------------------------

def format_date(d: date, rng: random.Random) -> str:
    style = rng.choice(["iso", "long", "short_month", "dmy", "mdy"])
    if style == "iso":
        return d.isoformat()
    if style == "long":
        return d.strftime("%B %d, %Y").replace(" 0", " ")
    if style == "short_month":
        return d.strftime("%d-%b-%Y")
    if style == "dmy":
        return d.strftime("%d/%m/%Y")
    return d.strftime("%m/%d/%Y")


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


INK = (30, 30, 34)
MUTED = (95, 95, 100)


def _paper(width: int, height: int, rng: random.Random) -> Image.Image:
    tint = rng.randint(242, 255)
    return Image.new("RGB", (width, height), (tint, tint, max(tint - rng.randint(0, 10), 230)))


def fit_font(draw: ImageDraw.ImageDraw, text: str, family: str, size: int, max_width: int, min_size: int = 12):
    """Largest font of `family`, up to `size`, that fits `text` in `max_width` pixels."""
    while size > min_size and draw.textlength(text, font=font(family, size)) > max_width:
        size -= 1
    return font(family, size)


def _center(draw: ImageDraw.ImageDraw, y: int, text: str, fnt, width: int, fill=INK) -> None:
    w = draw.textlength(text, font=fnt)
    draw.text(((width - w) / 2, y), text, font=fnt, fill=fill)


def _right(draw: ImageDraw.ImageDraw, x_right: int, y: int, text: str, fnt, fill=INK) -> None:
    w = draw.textlength(text, font=fnt)
    draw.text((x_right - w, y), text, font=fnt, fill=fill)


def _practitioner_line(f: dict) -> str:
    return f"{f['practitioner_name']}, {f['credential']}"


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------

def _classic(f: dict, rng: random.Random) -> Image.Image:
    W, H = 850, 1100
    img = _paper(W, H, rng)
    d = ImageDraw.Draw(img)
    y = 70
    _center(d, y, f["clinic_name"], fit_font(d, f["clinic_name"], "sans_bold", 34, W - 140), W); y += 52
    _center(d, y, f["clinic_address"], font("sans", 18), W, MUTED); y += 28
    _center(d, y, f"Tel: {f['clinic_phone']}", font("sans", 18), W, MUTED); y += 50
    d.line([(70, y), (W - 70, y)], fill=INK, width=2); y += 30
    _center(d, y, "OFFICIAL RECEIPT", font("sans_bold", 26), W); y += 60

    label, value = font("sans_bold", 20), font("sans", 20)
    rows = [
        ("Receipt #:", f["receipt_number"]),
        ("Date of service:", format_date(f["service_date"], rng)),
        ("Patient:", f["patient_name"]),
        ("Practitioner:", _practitioner_line(f)),
    ]
    if f["registration_number"]:
        rows.append(("Registration #:", f["registration_number"]))
    for k, v in rows:
        d.text((90, y), k, font=label, fill=INK)
        d.text((320, y), v, font=value, fill=INK)
        y += 38

    y += 30
    d.rectangle([(70, y), (W - 70, y + 40)], fill=(225, 225, 228))
    d.text((90, y + 9), "Description", font=label, fill=INK)
    _right(d, W - 90, y + 9, "Amount", label)
    y += 58
    d.text((90, y), f["service_description"], font=value, fill=INK)
    _right(d, W - 90, y, money(f["amount_cents"]), value)
    y += 60
    d.line([(70, y), (W - 70, y)], fill=INK, width=1); y += 20
    d.text((520, y), "Total:", font=font("sans_bold", 24), fill=INK)
    _right(d, W - 90, y, money(f["amount_cents"]), font("sans_bold", 24)); y += 40
    d.text((520, y), "Paid:", font=value, fill=INK)
    _right(d, W - 90, y, money(f["amount_cents"]), value); y += 34
    d.text((520, y), "Balance:", font=value, fill=INK)
    _right(d, W - 90, y, "$0.00", value); y += 60

    d.text((90, y), f"Payment method: {f['payment_method']}", font=value, fill=INK); y += 80
    d.line([(90, y), (420, y)], fill=INK, width=1)
    d.text((90, y + 8), "Practitioner signature", font=font("sans", 16), fill=MUTED)
    _center(d, H - 90, "Health services are exempt from HST.", font("sans", 16), W, MUTED)
    _center(d, H - 64, "Thank you for choosing us!", font("sans", 16), W, MUTED)
    return img


def _pos(f: dict, rng: random.Random) -> Image.Image:
    W, H = 520, 900
    img = _paper(W, H, rng)
    d = ImageDraw.Draw(img)
    m, big, small = font("mono", 19), font("mono", 24), font("mono", 16)
    dash = "-" * 38
    y = 40
    name = f["clinic_name"].upper()
    _center(d, y, name, fit_font(d, name, "mono", 24, W - 50), W); y += 40
    for line in f["clinic_address"].split(", ", 1):
        _center(d, y, line, small, W); y += 24
    _center(d, y, f["clinic_phone"], small, W); y += 36
    _center(d, y, dash, m, W); y += 30

    def kv(k: str, v: str) -> None:
        nonlocal y
        d.text((30, y), k, font=m, fill=INK)
        _right(d, W - 30, y, v, m)
        y += 30

    kv("RCPT", f["receipt_number"])
    kv("DATE", format_date(f["service_date"], rng))
    kv("CLIENT", f["patient_name"][:22])
    d.text((30, y), "SERVICE", font=m, fill=INK); y += 28
    d.text((30, y), f["service_description"][:36], font=m, fill=INK); y += 30
    d.text((30, y), "PROVIDER", font=m, fill=INK); y += 28
    d.text((30, y), _practitioner_line(f)[:36], font=m, fill=INK); y += 30
    if f["registration_number"]:
        kv("REG NO", f["registration_number"])
    _center(d, y, dash, m, W); y += 34
    d.text((30, y), "TOTAL", font=big, fill=INK)
    _right(d, W - 30, y, money(f["amount_cents"]), big); y += 44
    kv(f["payment_method"].upper()[:16], money(f["amount_cents"]))
    kv("CHANGE", "$0.00")
    _center(d, y, dash, m, W); y += 34
    _center(d, y, "HST EXEMPT", small, W); y += 26
    _center(d, y, "KEEP FOR YOUR INSURANCE", small, W)
    # Receipt paper is cut where the printing stops.
    return img.crop((0, 0, W, min(H, y + 60)))


def _invoice(f: dict, rng: random.Random) -> Image.Image:
    W, H = 850, 1100
    img = _paper(W, H, rng)
    d = ImageDraw.Draw(img)
    title, body, bold = font("serif_bold", 44), font("serif", 19), font("serif_bold", 19)
    accent = rng.choice([(30, 70, 120), (20, 100, 90), (110, 40, 60), (40, 40, 40)])

    d.rectangle([(0, 0), (W, 16)], fill=accent)
    # Clinic block on the left, kept clear of the INVOICE title on the right.
    left_width = W - 140 - int(d.textlength("INVOICE", font=title)) - 40
    d.text((70, 60), f["clinic_name"], font=fit_font(d, f["clinic_name"], "serif_bold", 28, left_width),
           fill=accent)
    y = 102
    for line in f["clinic_address"].split(", ", 1) + [f["clinic_phone"]]:
        d.text((70, y), line, font=fit_font(d, line, "serif", 19, left_width), fill=MUTED)
        y += 26
    _right(d, W - 70, 56, "INVOICE", title, accent)
    _right(d, W - 70, 116, f"No. {f['receipt_number']}", body)

    y = 210
    d.text((70, y), "BILLED TO", font=font("sans_bold", 15), fill=MUTED)
    d.text((70, y + 24), f["patient_name"], font=bold, fill=INK)
    d.text((470, y), "TREATING PRACTITIONER", font=font("sans_bold", 15), fill=MUTED)
    d.text((470, y + 24), _practitioner_line(f), font=bold, fill=INK)
    if f["registration_number"]:
        d.text((470, y + 50), f"Reg. {f['registration_number']}", font=body, fill=INK)

    y = 340
    d.rectangle([(70, y), (W - 70, y + 42)], fill=accent)
    for x, h in [(84, "Date"), (260, "Service")]:
        d.text((x, y + 10), h, font=font("sans_bold", 18), fill=(255, 255, 255))
    _right(d, W - 84, y + 10, "Fee", font("sans_bold", 18), (255, 255, 255))
    y += 60
    d.text((84, y), format_date(f["service_date"], rng), font=body, fill=INK)
    d.text((260, y), f["service_description"], font=body, fill=INK)
    _right(d, W - 84, y, money(f["amount_cents"]), body)
    y += 50
    d.line([(70, y), (W - 70, y)], fill=(200, 200, 200), width=1)

    y += 30
    for k, v, fnt in [("Subtotal", money(f["amount_cents"]), body),
                      ("HST (exempt)", "$0.00", body),
                      ("Total", money(f["amount_cents"]), font("serif_bold", 24))]:
        d.text((500, y), k, font=fnt, fill=INK)
        _right(d, W - 84, y, v, fnt)
        y += 40

    y += 30
    d.text((70, y), f"Paid in full by {f['payment_method']}.", font=body, fill=INK)

    # A tilted "PAID" stamp, as many clinics use.
    stamp = Image.new("RGBA", (260, 110), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.rounded_rectangle([(4, 4), (256, 106)], radius=12, outline=(180, 40, 40, 170), width=5)
    sd.text((50, 18), "PAID", font=font("sans_bold", 62), fill=(180, 40, 40, 170))
    stamp = stamp.rotate(rng.uniform(-18, 18), expand=True)
    img.paste(stamp, (rng.randint(470, 540), rng.randint(720, 800)), stamp)
    return img


_RENDERERS = {"classic": _classic, "pos": _pos, "invoice": _invoice}


def render_receipt(fields: dict, rng: random.Random, layout: str | None = None) -> Image.Image:
    layout = layout or rng.choice(LAYOUTS)
    return _RENDERERS[layout](fields, rng)


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------

def degrade(img: Image.Image, rng: random.Random, severity: str = "mild") -> Image.Image:
    np_rng = np.random.default_rng(rng.getrandbits(32))
    background = tuple([rng.randint(150, 205)] * 3)   # the table the receipt lies on

    if severity == "heavy":
        angle, blur, noise = rng.uniform(4, 8) * rng.choice([-1, 1]), rng.uniform(1.0, 1.8), 14
        img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.5, 0.7))
        # Shrink then enlarge to lose detail, like a far-away photo.
        w, h = img.size
        scale = rng.uniform(0.45, 0.6)
        img = img.resize((int(w * scale), int(h * scale)), Image.BILINEAR).resize((w, h), Image.BILINEAR)
    else:
        angle, blur, noise = rng.uniform(-1.5, 1.5), 0.0, 4

    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=background)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    arr = np.asarray(img).astype(np.float32)
    if severity == "heavy":
        # A soft shadow across one side of the page.
        h, w = arr.shape[:2]
        ramp = np.linspace(rng.uniform(0.55, 0.75), 1.0, w)
        if rng.random() < 0.5:
            ramp = ramp[::-1]
        arr *= ramp[None, :, None]
    arr += np_rng.normal(0, noise, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def to_jpeg(img: Image.Image, rng: random.Random, severity: str = "mild") -> bytes:
    quality = rng.randint(22, 38) if severity == "heavy" else rng.randint(78, 92)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def contact_sheet(paths: list[Path], columns: int = 4, thumb_width: int = 300) -> Image.Image:
    """Grid of thumbnails, handy for eyeballing generated receipts."""
    thumbs = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        ratio = thumb_width / im.width
        thumbs.append(im.resize((thumb_width, int(im.height * ratio))))
    rows = (len(thumbs) + columns - 1) // columns
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (columns * (thumb_width + 10) + 10, rows * (cell_h + 10) + 10), (235, 235, 235))
    for i, t in enumerate(thumbs):
        r, c = divmod(i, columns)
        sheet.paste(t, (10 + c * (thumb_width + 10), 10 + r * (cell_h + 10)))
    return sheet

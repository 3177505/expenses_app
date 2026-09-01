import io
import shutil
from pathlib import Path

import fitz
from PIL import Image, ImageOps

_tesseract_cmd_set = False
_OCR_FALLBACK_LANG = "deu+eng+ces"
_TESS_CONFIG = "--oem 3 --psm 6"
_MIN_OCR_SIDE = 1200
_PDF_ZOOM = 3.0


def _ensure_tesseract_cmd() -> None:
    global _tesseract_cmd_set
    if _tesseract_cmd_set:
        return
    import pytesseract

    which = shutil.which("tesseract")
    for cmd in (which, "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if cmd and Path(cmd).is_file():
            pytesseract.pytesseract.tesseract_cmd = cmd
            break
    _tesseract_cmd_set = True


try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    pass


def _prepare_image(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img) or img
    gray = img.convert("L")
    gray = ImageOps.autocontrast(gray)
    w, h = gray.size
    shortest = min(w, h)
    if shortest > 0 and shortest < _MIN_OCR_SIDE:
        scale = _MIN_OCR_SIDE / shortest
        gray = gray.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    return gray


def _ocr_image(img: Image.Image, lang: str) -> str:
    import pytesseract

    _ensure_tesseract_cmd()
    gray = _prepare_image(img)
    langs = [lang]
    if lang != _OCR_FALLBACK_LANG:
        langs.append(_OCR_FALLBACK_LANG)
    langs.append("eng")
    seen = set()
    last_err = None
    for L in langs:
        if not L or L in seen:
            continue
        seen.add(L)
        try:
            return pytesseract.image_to_string(gray, lang=L, config=_TESS_CONFIG)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return ""


def _embedded_text_ok(t: str) -> bool:
    s = (t or "").strip()
    if len(s) < 40:
        return False
    letters = sum(ch.isalpha() for ch in s)
    if letters < 20:
        return False
    if letters / len(s) < 0.18:
        return False
    if "\ufffd" in s:
        return False
    return True


def extract_text(path: Path, ocr_lang: str = "deu+eng+ces") -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        doc = fitz.open(path)
        parts: list[str] = []
        try:
            for page in doc:
                t = page.get_text() or ""
                if _embedded_text_ok(t):
                    parts.append(t)
                    continue
                mat = fitz.Matrix(_PDF_ZOOM, _PDF_ZOOM)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                png = pix.tobytes("png")
                img = Image.open(io.BytesIO(png))
                parts.append(_ocr_image(img, ocr_lang))
        finally:
            doc.close()
        return "\n".join(parts)

    if suffix in {".heic", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
        with Image.open(path) as img:
            return _ocr_image(img, ocr_lang)

    raise ValueError(f"unsupported type: {suffix}")

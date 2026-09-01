import io
import shutil
from pathlib import Path

import fitz
from PIL import Image

_tesseract_cmd_set = False


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


def _ocr_image(img: Image.Image, lang: str) -> str:
    import pytesseract

    _ensure_tesseract_cmd()
    gray = img.convert("L")
    try:
        return pytesseract.image_to_string(gray, lang=lang)
    except Exception:
        return pytesseract.image_to_string(gray, lang="deu+eng")


def extract_text(path: Path, ocr_lang: str = "deu+eng") -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        doc = fitz.open(path)
        parts: list[str] = []
        try:
            for page in doc:
                t = page.get_text()
                if t and len(t.strip()) >= 30:
                    parts.append(t)
                else:
                    mat = fitz.Matrix(2.0, 2.0)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    png = pix.tobytes("png")
                    img = Image.open(io.BytesIO(png))
                    parts.append(_ocr_image(img, ocr_lang))
        finally:
            doc.close()
        return "\n".join(parts)

    if suffix in {".heic", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
        img = Image.open(path)
        return _ocr_image(img, ocr_lang)

    raise ValueError(f"unsupported type: {suffix}")

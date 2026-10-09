"""Turn an upload (photo, PDF, plain text) or pasted text into a Source the model can read."""

import base64
import io
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from pypdf import PdfReader
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import MAX_IMAGES, MAX_TEXT_CHARS, MAX_UPLOAD_BYTES

log = logging.getLogger("notice.backend.ingest")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # 'invalid pdf header' noise on bad uploads

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
PDF_MIMES = {"application/pdf"}


class IngestError(ValueError):
    """Raised when an upload cannot be interpreted."""


@dataclass
class Page:
    number: int
    text: str


@dataclass
class Source:
    kind: str                      # image | pdf | text
    filename: str = ""
    content_type: str = ""
    pages: List[Page] = field(default_factory=list)
    images: List[str] = field(default_factory=list)   # base64, no data: prefix
    text: str = ""
    scanned: bool = False          # PDF with no extractable text
    truncated: bool = False

    @property
    def source_ref(self) -> str:
        if self.kind == "image":
            return "image"
        if self.kind == "pdf":
            return "page 1"
        return "pasted text"

    @property
    def page_count(self) -> int:
        return len(self.pages) or (len(self.images) if self.kind == "image" else 1)

    def summary(self) -> dict:
        return {
            "kind": self.kind,
            "filename": self.filename,
            "pages": self.page_count,
            "chars": len(self.text),
            "images": len(self.images),
            "scanned": self.scanned,
            "truncated": self.truncated,
        }


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _from_pdf(filename: str, data: bytes) -> Source:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises a grab-bag of errors
        raise IngestError(f"could not read PDF: {exc}") from exc

    if reader.is_encrypted:
        raise IngestError("Password-protected PDF: upload an unlocked copy.")
    if not reader.pages:
        raise IngestError("PDF has no pages.")
    if len(reader.pages) > 30:
        raise IngestError("PDF exceeds 30 pages. Split the document first.")
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - a broken page must not kill the run
            log.warning("page %s extraction failed: %s", index, exc)
            page_text = ""
        pages.append(Page(number=index, text=page_text.strip()))

    source = Source(kind="pdf", filename=filename, content_type="application/pdf", pages=pages)
    source.text = "\n".join(f"[page {p.number}]\n{p.text}" for p in pages if p.text)
    extractable = sum(len(p.text) for p in pages)
    source.scanned = not extractable
    if source.scanned:
        raise IngestError("PDF has no selectable text. Enable scanned-page OCR in Reading options, review and save the text, then choose Analyze saved text.")
    if any(not page.text for page in pages):
        raise IngestError("Some PDF pages have no selectable text. Use scanned-page OCR, review all pages, then Analyze saved text to avoid omitting pages.")
    if len(source.text) > MAX_TEXT_CHARS:
        raise IngestError("PDF text exceeds the configured limit. Split the document.")
    return source


def _from_image(filename: str, content_type: str, data: bytes) -> Source:
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(f"image larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 25_000_000:
                raise IngestError("Image exceeds 25 million pixels. Resize it first.")
            if getattr(image, "n_frames", 1) > 1:
                raise IngestError("Multi-frame image: upload each page as a separate image or PDF.")
            image.load()
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            output = io.BytesIO()
            normalized.save(output, format="PNG")
            data = output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise IngestError("Cannot decode this image. Upload a valid PNG/JPG.") from exc
    return Source(
        kind="image",
        filename=filename,
        content_type=content_type,
        images=[base64.b64encode(data).decode("ascii")],
        text="",
    )


def _from_text(filename: str, content_type: str, data: bytes) -> Source:
    text = _decode(data)
    from_text(text)
    return Source(kind="text", filename=filename, content_type=content_type, text=text)


def read_upload(filename: str, content_type: str, data: bytes) -> Source:
    if not data:
        raise IngestError("empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    lowered = (filename or "").lower()
    ext = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    ctype = (content_type or "").split(";")[0].strip().lower()

    if ctype in IMAGE_MIMES or ext in IMAGE_EXTS:
        return _from_image(filename or "notice", ctype, data)
    if ctype in PDF_MIMES or ext == ".pdf":
        return _from_pdf(filename or "notice.pdf", data)
    if ext == ".txt" or (not ext and ctype == "text/plain"):
        return _from_text(filename or "notice.txt", ctype, data)
    raise IngestError("Unsupported file type. Use PDF, a supported image, or TXT.")


def from_text(text: str) -> Source:
    if not text or not text.strip():
        raise IngestError("no text provided")
    if len(text) > MAX_TEXT_CHARS:
        raise IngestError("Text exceeds the configured character limit. Split the notice.")
    return Source(kind="text", filename="pasted text", content_type="text/plain", text=text)


def _truncate(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS], True


def document_block(source: Source) -> str:
    """Render the notice for the prompt, keeping page references intact."""
    if source.kind == "image":
        return (
            "The notice is attached as an image. Read it visually: title, dates, venue, "
            "eligibility, documents, fees, contacts, links and any fine print. "
            "Use source_ref \"image\" for every quote."
        )

    body, truncated = _truncate(source.text)
    source.truncated = source.truncated or truncated

    if source.kind == "pdf":
        header = f'PDF notice "{source.filename}", {source.page_count} page(s).'
        if source.scanned:
            return (
                f"{header} No extractable text: this looks like a scanned/image-only PDF, "
                "so the text layer is empty. Do NOT guess the content. Put every field that "
                "would normally be extracted into `missing` with issue \"unreadable\", set "
                "needs_clearer_image=true, and say in `answer` that a photo of the page or "
                "the pasted text is needed."
            )
        return f"{header} Text extracted per page:\n\n{body or '(no text)'}"

    return f'Pasted text notice:\n\n{body or "(empty)"}'


def image_payload(source: Source) -> List[str]:
    return source.images[:MAX_IMAGES]


def source_ref_for(source: Source, page_hint: Optional[int] = None) -> str:
    if source.kind == "image":
        return "image"
    if source.kind == "pdf":
        return f"page {page_hint}" if page_hint else "page 1"
    return "pasted text"

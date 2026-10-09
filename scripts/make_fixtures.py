"""Generate local test fixtures: a scholarship notice as .txt, .png and .pdf."""

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "fixtures"
OUT.mkdir(exist_ok=True)

NOTICE = """ST. XAVIER'S COLLEGE (AUTONOMOUS)
MERIT-CUM-MEANS SCHOLARSHIP 2026-27
Notice No. SCH/2026/41                    Date: 3 October 2026

Applications are invited from enrolled students for the Merit-cum-Means
Scholarship for the academic year 2026-27.

ELIGIBILITY
- Minimum 75% aggregate in the last completed examination
- Family annual income below Rs. 8,00,000
- Minimum 75% attendance in the current semester

DOCUMENTS REQUIRED (attach self-attested copies)
1. Mark sheet of the last examination        [REQUIRED]
2. Income certificate / ITR of guardian       [REQUIRED]
3. Caste certificate, if applicable           [REQUIRED]
4. Sports / NCC certificate (for extra weightage)  [OPTIONAL]

FEES
Application processing fee: Rs. 100 (non-refundable), payable online.

HOW TO APPLY
Submit the completed form with documents to the SCHOLARSHIP OFFICE,
Admin Block, Room 12, on or before 15 November 2026, 4:00 PM.
Online registration: https://example.edu/scholarship/apply

Scholarship Office
Admin Block, Room 12
Phone: +91 22 5550 0101
Email: scholarships@example.edu
Office hours: Mon-Fri 10:00-17:00
"""


def write_text() -> None:
    (OUT / "notice.txt").write_text(NOTICE, encoding="utf-8")


def write_image() -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, padding = 1000, 48
    probe = Image.new("RGB", (width, 10), "white")
    draw = ImageDraw.Draw(probe)

    def load(size: int):
        for path in (
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNS.ttf",
            "/Library/Fonts/Arial.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    font = load(22)
    title_font = load(30)

    lines = []
    for raw in NOTICE.splitlines():
        if not raw.strip():
            lines.append(("", font))
            continue
        head = load(26) if raw.startswith(("ST. XAVIER", "MERIT")) else (title_font if raw.isupper() else font)
        lines.append((raw, head))

    height = padding * 2
    for text, f in lines:
        bbox = draw.textbbox((0, 0), text or " ", font=f)
        height += (bbox[3] - bbox[1]) + 8

    image = Image.new("RGB", (width, height + 20), "white")
    draw = ImageDraw.Draw(image)
    y = padding
    for text, f in lines:
        draw.text((padding, y), text, fill="black", font=f)
        bbox = draw.textbbox((0, 0), text or " ", font=f)
        y += (bbox[3] - bbox[1]) + 8
    image.save(OUT / "notice.png")
    print("wrote", OUT / "notice.png", image.size)


def write_pdf() -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in NOTICE.splitlines():
        pdf.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(OUT / "notice.pdf"))
    print("wrote", OUT / "notice.pdf")


def main() -> int:
    write_text()
    try:
        write_image()
    except ImportError:
        print("Pillow not installed - skipping image fixture", file=sys.stderr)
    try:
        write_pdf()
    except ImportError:
        print("fpdf2 not installed - skipping PDF fixture", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

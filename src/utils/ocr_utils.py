import pytesseract
from PIL import Image
import re

def extract_salary_from_image(image_path: str) -> int | None:
    """Extract approximate salary number using OCR."""
    try:
        text = pytesseract.image_to_string(Image.open(image_path))
        match = re.search(r"(\d{4,6})", text.replace(",", ""))
        if match:
            return int(match.group(1))
        return None
    except Exception as e:
        print(f"[OCR ERROR] {e}")
        return None

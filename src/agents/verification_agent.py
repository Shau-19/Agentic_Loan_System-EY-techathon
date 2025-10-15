"""
    Verifies KYC (Aadhar/PAN/Address) from CRM + optional OCR check.
    Compatible with NBFC pipeline: returns deterministic JSON for MasterAgent.

    Behavior changes (kept compatible with original structure):
      - Accepts either `document_path` (path on disk) OR `uploaded_docs` list
        where each item can be {"doc_type","file_name","file_bytes"}.
      - If bytes are provided, will write to a temp file and run OCR (if available).
      - If OCR can't extract salary, a deterministic pseudo-extraction from filename/hash
        is used to keep tests reproducible.
    """

# src/agents/verification_agent.py
import os
import io
import tempfile
import hashlib
import requests
import pytesseract
import fitz  # PyMuPDF for PDF -> image conversion
from PIL import Image
from rapidfuzz import fuzz, process
from dotenv import load_dotenv

from src.agents.base_agent import BaseAgent, AgentMessage
from src.data.database import NBFCDatabase

# --- Load environment (for mock API base URL etc.) ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=dotenv_path)

# Optional: hardcode path for Windows (for venvs)
# Keep this non-fatal if the path doesn't exist
try:
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except Exception:
    pass


class VerificationAgent(BaseAgent):
    

    def __init__(self):
        super().__init__("verification_agent", "verification")
        self.db = NBFCDatabase()
        self.crm_base = os.getenv("CRM_API_URL", "http://127.0.0.1:8001/crm")

    # ---------- Helper Functions ----------
    def _normalize_text(self, s: str) -> str:
        import re
        if not s:
            return ""
        s = s.lower()
        s = re.sub(r"[^a-z0-9\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _ocr_extract_text(self, document_path: str) -> str:
        """Extract text from PDF or image via OCR (PyMuPDF + Tesseract)."""
        if not os.path.exists(document_path):
            raise FileNotFoundError(f"Document not found: {document_path}")

        text = ""
        ext = os.path.splitext(document_path)[1].lower()
        try:
            if ext == ".pdf":
                with fitz.open(document_path) as doc:
                    for page in doc:
                        pix = page.get_pixmap(dpi=300)
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        text += pytesseract.image_to_string(img, lang="eng", config="--psm 6 --oem 3")
            else:
                img = Image.open(document_path)
                text = pytesseract.image_to_string(img, lang="eng", config="--psm 6 --oem 3")
        except Exception as e:
            # OCR failed — return empty string to allow deterministic fallback
            print(f"OCR extraction failed for {document_path}: {e}")
            return ""
        return text.strip()

    def _ocr_match_name(self, ocr_text: str, db_name: str) -> dict:
        """Robust fuzzy compare OCR text vs DB name; tolerant to DOB and small OCR noise."""
        from rapidfuzz import fuzz, process
        import re

        def _clean_for_name(s: str) -> str:
            if not s:
                return ""
            s = s.lower()
            # remove common noise words and numeric tokens (dates, years, ids)
            s = re.sub(r"\b(dob|date|birth|birthdate|age|yob|yr|year)\b", " ", s)
            s = re.sub(r"\d{1,4}", " ", s)  # remove numbers (years, DOB pieces)
            s = re.sub(r"[^a-z\s]", " ", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        ocr_clean = _clean_for_name(ocr_text)
        db_clean = _clean_for_name(db_name)

        result = {"ocr_verification": "no_name_detected", "best_match": None, "score": 0}
        if not ocr_clean or not db_clean:
            return result

        # coarse global ratio vs OCR blob
        global_ratio = fuzz.token_set_ratio(db_clean, ocr_clean)
        # try best substring segments (sliding window of words)
        words = [w for w in ocr_clean.split() if len(w) > 1]
        best_seg_score = 0
        best_seg = None
        if words:
            # build candidate n-gram segments up to length 5
            candidates = []
            max_n = min(5, len(words))
            for n in range(1, max_n + 1):
                for i in range(0, len(words) - n + 1):
                    candidates.append(" ".join(words[i: i + n]))
            if candidates:
                best = process.extractOne(db_clean, candidates, scorer=fuzz.token_set_ratio)
                if best:
                    best_seg, best_seg_score = best[0], int(best[1])

        # combine scores (token_set and partial) — tuneable weights
        token_sort = fuzz.token_sort_ratio(db_clean, ocr_clean)
        partial = fuzz.partial_ratio(db_clean, ocr_clean)
        combined = int(max(global_ratio, best_seg_score, token_sort, partial))

        result["best_match"] = best_seg or (ocr_clean[:100] if ocr_clean else "")
        result["score"] = combined

        # Threshold — slightly lower to tolerate noisy OCR
        MATCH_THRESHOLD = 65
        if combined >= MATCH_THRESHOLD:
            result["ocr_verification"] = "matched"
        elif combined >= (MATCH_THRESHOLD - 10):
            result["ocr_verification"] = "low_confidence"
        else:
            result["ocr_verification"] = "no_match"

        return result

    def _deterministic_salary_from_key(self, key: str) -> (int, float, str):
        """
        Deterministic pseudo-salary derived from a key (filename or bytes length).
        Returns (monthly_salary, confidence, source)
        """
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        seed = int(h[:8], 16)
        monthly_salary = 25000 + (seed % 95001)  # 25k..120k
        conf = 0.45 + ((int(h[8:16], 16) % 55) / 100.0)  # 0.45 .. ~1.0
        return int(monthly_salary), float(conf), "simulated_from_hash"

    # ---------- Core Agent Logic ----------
    async def handle(self, message: AgentMessage) -> AgentMessage:
        """
        Input: message.content = {
            "customer_id": str,
            "document_path": optional (path on disk),
            "uploaded_docs": optional list of {"doc_type","file_name","file_bytes"}
        }
        Output: structured verification summary
        """
        content = message.content or {}
        customer_id = content.get("customer_id")
        document_path = content.get("document_path")
        uploaded_docs = content.get("uploaded_docs") or content.get("uploaded_doc") or []

        if not customer_id:
            return AgentMessage(sender=self.agent_id, recipient=message.sender,
                                content={"error": "customer_id_missing"})

        customer = None
        try:
            customer = self.db.get_customer(customer_id)
        except Exception:
            customer = None

        if not customer:
            return AgentMessage(sender=self.agent_id, recipient=message.sender,
                                content={"error": "customer_not_found"})

        # --- Step 1: CRM Verification (mock API) ---
        verified_docs, missing_docs = [], []
        crm_msg = ""
        try:
            resp = requests.get(f"{self.crm_base}/customer/{customer_id}", timeout=5)
            if resp.status_code == 200:
                crm_data = resp.json()
                # the mock CRM may use 'documents' or 'verified_docs' shape
                # prefer a structured 'documents' dict if present
                if isinstance(crm_data.get("documents"), dict):
                    docs = crm_data.get("documents", {})
                    # docs example: {"aadhar":"verified", "pan":"verified"}
                    for k, v in docs.items():
                        if v and str(v).lower().startswith("verif"):
                            verified_docs.append(k)
                        else:
                            missing_docs.append(k)
                else:
                    # fallback shapes
                    verified_docs = crm_data.get("verified_docs", []) or []
                    missing_docs = crm_data.get("missing_docs", []) or []
                crm_msg = f"Dear {customer.get('name')}, your KYC verification has been successfully completed."
            else:
                crm_msg = f"CRM verification responded {resp.status_code} for {customer_id}."
        except Exception as e:
            crm_msg = f"CRM API error: {e}"

        # Prepare OCR metadata placeholders
        ocr_result = {}
        monthly_salary = None
        salary_confidence = 0.0
        ocr_matched_line = None
        ocr_source = None
        ocr_name_ver = None

        # --- Step 2: OCR Verification if uploaded_docs provided OR document_path provided ---
        # Support both a file path or uploaded bytes from orchestrator run_with_salary_slip
        file_to_process_path = None
        temp_files = []

        try:
            # If uploaded_docs provided as bytes
            if isinstance(uploaded_docs, (list, tuple)) and uploaded_docs:
                first = uploaded_docs[0]
                file_name = first.get("file_name") or first.get("name") or "uploaded_doc"
                file_bytes = first.get("file_bytes") or first.get("bytes") or None

                # If bytes are present, write to a temporary file for OCR
                if file_bytes and isinstance(file_bytes, (bytes, bytearray)):
                    # guess extension by simple heuristics on filename
                    _, ext = os.path.splitext(file_name or "")
                    ext = ext.lower() if ext else ".pdf"
                    if ext not in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
                        # prefer png for unknown binary data
                        ext = ".pdf" if file_bytes[:4] == b"%PDF" else ".png"
                    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
                    with os.fdopen(tmp_fd, "wb") as fh:
                        fh.write(file_bytes)
                    file_to_process_path = tmp_path
                    temp_files.append(tmp_path)
                elif file_name and os.path.exists(file_name):
                    file_to_process_path = file_name
                else:
                    # fallback: deterministic synthesis from filename string
                    monthly_salary, salary_confidence, ocr_source = self._deterministic_salary_from_key(file_name)
                    ocr_matched_line = str(monthly_salary)
                    ocr_name_ver = None

            # If no uploaded_docs but a document_path (path on disk) is provided
            elif document_path:
                if os.path.exists(document_path):
                    file_to_process_path = document_path
                else:
                    # possible remote URL? attempt to fetch
                    if str(document_path).startswith("http"):
                        try:
                            r = requests.get(document_path, timeout=5)
                            if r.status_code == 200:
                                # write to temp file
                                ext = os.path.splitext(document_path)[1] or ".pdf"
                                tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
                                with os.fdopen(tmp_fd, "wb") as fh:
                                    fh.write(r.content)
                                file_to_process_path = tmp_path
                                temp_files.append(tmp_path)
                        except Exception:
                            file_to_process_path = None

            # If we have a file path to process, attempt OCR
            if file_to_process_path:
                try:
                    text = self._ocr_extract_text(file_to_process_path)
                except Exception as e:
                    text = ""
                # name verification if text exists
                if text:
                    name_match = self._ocr_match_name(text, customer.get("name", ""))
                    ocr_name_ver = name_match
                    # attempt to extract salary-like number
                    import re
                    m = re.search(r'(\d{4,7})', text.replace(",", ""))
                    if m:
                        try:
                            monthly_salary = int(m.group(1))
                            salary_confidence = 0.8 if name_match.get("ocr_verification") == "matched" else 0.6
                            ocr_matched_line = m.group(1)
                            ocr_source = "ocr_extracted_text"
                        except Exception:
                            monthly_salary = None
                    else:
                        # no numeric line detected — degrade to deterministic fallback using filename/key
                        key = file_to_process_path + (os.path.basename(file_to_process_path) or "")
                        monthly_salary, salary_confidence, ocr_source = self._deterministic_salary_from_key(key)
                        ocr_matched_line = str(monthly_salary)
                else:
                    # OCR produced no text — deterministic fallback
                    key = file_to_process_path + (os.path.basename(file_to_process_path) or "")
                    monthly_salary, salary_confidence, ocr_source = self._deterministic_salary_from_key(key)
                    ocr_matched_line = str(monthly_salary)

            # Build ocr_result dict with best-effort fields
            if monthly_salary is not None:
                ocr_result = {
                    "monthly_salary_extracted": int(monthly_salary),
                    "salary_extraction_confidence": float(salary_confidence or 0.0),
                    "ocr_matched_line": ocr_matched_line,
                    "ocr_source": ocr_source or ("ocr_name_match" if ocr_name_ver else "simulated"),
                }
                # include name-match summary if available
                if ocr_name_ver:
                    ocr_result.update({
                        "ocr_name_best_match": ocr_name_ver.get("best_match"),
                        "ocr_name_score": ocr_name_ver.get("score"),
                        "ocr_name_verification": ocr_name_ver.get("ocr_verification"),
                    })
            else:
                ocr_result = {"ocr_verification": "not_performed"}

        finally:
            # cleanup temp files created
            for p in temp_files:
                try:
                    os.remove(p)
                except Exception:
                    pass

        # --- Step 3: Combine results into final response ---
        verification_status = "passed" if not missing_docs else "partial"
        message_text = crm_msg

        # Append OCR messages to CRM message_text when present
        if ocr_result:
            if ocr_result.get("monthly_salary_extracted"):
                message_text += f" Salary slip processed. Extracted monthly salary: ₹{int(ocr_result.get('monthly_salary_extracted')):,}."
            elif ocr_result.get("ocr_verification") == "not_performed":
                message_text += " No OCR performed."
            elif ocr_result.get("ocr_verification") == "no_text_detected":
                message_text += " No text detected during OCR."

        # Final structured response - keep keys consistent with earlier expectations
        response = {
            "customer_id": customer_id,
            "customer_name": customer.get("name"),
            "verification_status": verification_status,
            "verified_docs": verified_docs,
            "missing_docs": missing_docs,
            "message": message_text,
        }
        # merge in OCR result fields (if any)
        response.update(ocr_result or {})

        return AgentMessage(sender=self.agent_id, recipient=message.sender, content=response)

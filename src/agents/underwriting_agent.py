"""
    Underwriting Agent with improved OCR salary slip handling.
    Key improvements in this patch:
    - Better salary extraction via flexible regex matching (currencies, commas,
      keywords like 'gross monthly', 'Net pay', 'Monthly salary').
    - Returns explicit fields in the result: monthly_salary_used, ocr_confidence,
      ocr_matched_line, salary_extraction_source and a human-readable reason
      describing why loan was approved/rejected.
    - EMI is computed and returned and compared against salary, and the reason
      message contains the EMI, salary and the 50% threshold check result.
    - MANUAL REVIEW FLAGS for salary mismatches and low OCR confidence
    
    Existing behavior preserved where possible.
    """
import os
import io
import time
import random
import asyncio
import requests
import re
from math import ceil
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# OCR imports
try:
    import fitz  # PyMuPDF
    from PIL import Image
    import pytesseract
    TESSERACT_AVAILABLE = True
    # If Windows default path needed, you can set it here (adjust to your environment)
    # pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except Exception:
    fitz = None
    Image = None
    pytesseract = None
    TESSERACT_AVAILABLE = False

# Load .env if present
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=dotenv_path)

# Optional LLM import (guarded). If unavailable, deterministic fallback messages are used.
try:
    from langchain_groq import ChatGroq
    from langchain.schema import SystemMessage, HumanMessage
    LLM_AVAILABLE = True
except Exception:
    ChatGroq = None
    SystemMessage = None
    HumanMessage = None
    LLM_AVAILABLE = False

from src.agents.base_agent import BaseAgent, AgentMessage
from src.data.database import NBFCDatabase


class UnderwritingAgent(BaseAgent):
    

    def __init__(self, model_name: str = "llama3-8b-8192"):
        super().__init__("underwriting_agent", "underwriting")
        self.db = NBFCDatabase()
        self.llm = None
        if LLM_AVAILABLE:
            try:
                self.llm = ChatGroq(model=model_name, temperature=0.0)
            except Exception:
                self.llm = None

    def _emi(self, principal: float, annual_rate: float, months: int) -> float:
        """Standard EMI formula; returns numeric EMI (float)."""
        if months <= 0:
            return 0.0
        r = annual_rate / 12.0 / 100.0
        if r == 0:
            return principal / months
        emi = (principal * r * (1 + r) ** months) / ((1 + r) ** months - 1)
        return emi

    # -------------------------
    # OCR helpers
    # -------------------------
    def _ocr_extract_text_from_path(self, document_path: str) -> str:
        """Extract text from PDF/image path using PyMuPDF + pytesseract."""
        if not TESSERACT_AVAILABLE:
            return ""
        if not os.path.exists(document_path):
            return ""
        try:
            ext = os.path.splitext(document_path)[1].lower()
            text = ""
            if ext in (".pdf",) and fitz is not None:
                doc = fitz.open(document_path)
                for page in doc:
                    pix = page.get_pixmap()
                    img_bytes = pix.tobytes()
                    try:
                        im = Image.open(io.BytesIO(img_bytes))
                        text += pytesseract.image_to_string(im) + "\n"
                    except Exception:
                        continue
                doc.close()
            else:
                # treat as image
                try:
                    im = Image.open(document_path)
                    text = pytesseract.image_to_string(im)
                except Exception:
                    text = ""
            return text or ""
        except Exception:
            return ""

    def _ocr_extract_text_from_bytes(self, file_bytes: bytes) -> str:
        """Extract text from bytes (PDF or image) using PyMuPDF + pytesseract."""
        if not TESSERACT_AVAILABLE:
            return ""
        try:
            # Try to open as PDF first
            try:
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                text = ""
                for page in doc:
                    pix = page.get_pixmap()
                    img_bytes = pix.tobytes()
                    im = Image.open(io.BytesIO(img_bytes))
                    text += pytesseract.image_to_string(im) + "\n"
                doc.close()
                return text
            except Exception:
                # fallback to image
                try:
                    im = Image.open(io.BytesIO(file_bytes))
                    return pytesseract.image_to_string(im)
                except Exception:
                    return ""
        except Exception:
            return ""

    def _extract_salary_from_text(self, text: str) -> Dict[str, Any]:
        """
        Given OCR/text content, try to find a monthly salary amount.
        Returns dict: { 'monthly_salary': float|None, 'confidence': float (0..1), 'matched_line': str|None, 'source_hint': str }
        """
        if not text:
            return {"monthly_salary": None, "confidence": 0.0, "matched_line": None, "source_hint": "none"}

        # Normalize
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        joined = "\n".join(lines)

        # Patterns: currency symbol or words then number. Capture numbers like 150,000 or 150000.00
        currency_num = r"(?:₹|INR|Rs\.?|rs\.?|rs\s|rupees\s)?\s*([0-9][0-9,\.]+)"

        # Keywords often near salary
        keywords = [
            r"gross\s+monthly\s+salary",
            r"monthly\s+salary",
            r"net\s+pay",
            r"take[-\s]?home\s+pay",
            r"salary\s+for\s+the\s+month",
            r"total\s+earnings",
            r"basic\s+pay",
            r"gross\s+salary",
        ]

        # Search by lines with keywords first (higher confidence)
        for kw in keywords:
            pattern = re.compile(rf"({kw}.*?)({currency_num})", re.IGNORECASE)
            for line in lines:
                m = pattern.search(line)
                if m:
                    num = m.group(2)
                    val = re.sub(r"[^0-9.]", "", num)
                    try:
                        salary = float(val)
                        # heuristics: if number looks yearly (very large) divide? We'll just return as monthly if plausible
                        # Confidence high if keyword matched
                        return {"monthly_salary": salary, "confidence": 0.75, "matched_line": line, "source_hint": "keyword_line"}
                    except Exception:
                        continue

        # Fallback: look for any currency-like numbers and pick the largest reasonable one on the page
        amounts = []
        for line in lines:
            for m in re.finditer(currency_num, line, re.IGNORECASE):
                num = m.group(1)
                val = re.sub(r"[^0-9.]", "", num)
                try:
                    v = float(val)
                    amounts.append((v, line))
                except Exception:
                    continue

        if amounts:
            # pick the largest number (often gross/annual numbers may be bigger; still pick largest as candidate)
            amounts.sort(key=lambda x: x[0], reverse=True)
            v, matched_line = amounts[0]
            
            # If value looks like annual (e.g., > 200000), attempt simple heuristic: if there are lines mentioning 'per annum' or 'annual' use divide by 12
            if re.search(r"per\s+annum|annual|pa\b|p\.a\.", joined, re.IGNORECASE) and v > 50000:
                monthly = v / 12.0
                confidence = 0.45
                return {"monthly_salary": monthly, "confidence": confidence, "matched_line": matched_line, "source_hint": "annual_to_monthly_heuristic"}
            
            # Otherwise treat as monthly
            confidence = 0.35
            return {"monthly_salary": v, "confidence": confidence, "matched_line": matched_line, "source_hint": "largest_number"}

        return {"monthly_salary": None, "confidence": 0.0, "matched_line": None, "source_hint": "none"}

    # -------------------------
    # Handler
    # -------------------------
    async def handle(self, message: AgentMessage) -> AgentMessage:
        """
        message.content expects:
        - customer_id: str
        - loan_amount: number (required)
        - tenure_months: int (optional, default 36)
        - monthly_salary: number (optional, if verification provided it)
        - monthly_salary_from_docs: number (optional, orchestrator will set when doc present)
        - monthly_salary_from_db_estimate: number (optional)
        - uploaded_docs: list of {doc_type, file_name, file_bytes} (optional)
        - salary_slip_path: path on disk (optional)

        Returns AgentMessage with content dict containing decision details.
        """
        content: Dict[str, Any] = message.content or {}
        customer_id = content.get("customer_id")
        
        try:
            loan_amount = float(content.get("loan_amount", 0) or 0)
        except Exception:
            loan_amount = 0.0

        try:
            months = int(content.get("tenure_months", 36) or 36)
        except Exception:
            months = 36

        # Try to fetch customer
        customer = None
        if customer_id:
            customer = self.db.get_customer(customer_id)

        if not customer:
            return AgentMessage(sender=self.agent_id, recipient=message.sender, content={
                "decision": "rejected",
                "reasons": ["customer_not_found"],
                "message": "Customer not found in DB",
            })

        # Mock credit bureau call (best-effort)
        credit_score = int(customer.get("credit_score", 0) or 0)
        try:
            resp = requests.get(f"http://127.0.0.1:8001/credit-bureau/score/{customer_id}", timeout=5)
            if resp.status_code == 200:
                credit_score = int(resp.json().get("credit_score", credit_score))
        except Exception:
            # fallback DB score used
            pass

        pre_limit = float(customer.get("pre_approved_limit", 0) or 0)
        monthly_income_db = (customer.get("annual_income", 0) or 0) / 12.0

        # Prefer explicit monthly salary from caller/verification
        explicit_monthly_salary: Optional[float] = None
        if "monthly_salary" in content and content.get("monthly_salary") not in (None, ""):
            try:
                explicit_monthly_salary = float(content.get("monthly_salary"))
            except Exception:
                explicit_monthly_salary = None

        # Accept orchestrator-provided doc salary under several keys
        doc_monthly_salary: Optional[float] = None
        if "monthly_salary_from_docs" in content and content.get("monthly_salary_from_docs") not in (None, ""):
            try:
                doc_monthly_salary = float(content.get("monthly_salary_from_docs"))
            except Exception:
                doc_monthly_salary = None

        # some helpers may set monthly_salary_from_underwriting or monthly_salary_used
        if not doc_monthly_salary:
            for k in ("monthly_salary_from_underwriting", "monthly_salary_used", "extracted_monthly_salary"):
                if content.get(k) not in (None, ""):
                    try:
                        doc_monthly_salary = float(content.get(k))
                        break
                    except Exception:
                        continue

        # Attempt OCR extraction if uploaded docs or path provided and no doc_monthly_salary yet
        ocr_monthly_salary = None
        ocr_conf = 0.0
        ocr_line = None
        ocr_source = None

        uploaded_docs = content.get("uploaded_docs") or []
        if not doc_monthly_salary and uploaded_docs and isinstance(uploaded_docs, (list, tuple)):
            for d in uploaded_docs:
                try:
                    if (d.get("doc_type") or "").lower() == "salary_slip" and d.get("file_bytes"):
                        text = self._ocr_extract_text_from_bytes(d.get("file_bytes"))
                        res = self._extract_salary_from_text(text)
                        if res.get("monthly_salary"):
                            ocr_monthly_salary = float(res["monthly_salary"])
                            ocr_conf = float(res.get("confidence", 0.0))
                            ocr_line = res.get("matched_line")
                            ocr_source = res.get("source_hint")
                            break
                except Exception:
                    continue

        if ocr_monthly_salary is None and content.get("salary_slip_path"):
            path = content.get("salary_slip_path")
            try:
                text = self._ocr_extract_text_from_path(path)
                res = self._extract_salary_from_text(text)
                if res.get("monthly_salary"):
                    ocr_monthly_salary = float(res["monthly_salary"])
                    ocr_conf = float(res.get("confidence", 0.0))
                    ocr_line = res.get("matched_line")
                    ocr_source = res.get("source_hint")
            except Exception:
                pass

        # Determine which salary to use (priority):
        # 1) orchestrator/doc-provided monthly_salary_from_docs or monthly_salary_from_underwriting
        # 2) explicit monthly_salary passed in content
        # 3) OCR extraction performed here (ocr_monthly_salary)
        # 4) DB estimate (monthly_income_db)
        monthly_salary = None
        income_provenance = None

        if doc_monthly_salary:
            monthly_salary = doc_monthly_salary
            income_provenance = "doc_provided"
            # if orchestrator passed it, we may not have ocr metadata here; attempt to pick from content keys
            ocr_conf = float(content.get("ocr_confidence") or content.get("salary_extraction_confidence") or ocr_conf or 0.0)
            ocr_line = content.get("ocr_matched_line") or ocr_line
            ocr_source = content.get("ocr_source") or ocr_source
        elif explicit_monthly_salary:
            monthly_salary = explicit_monthly_salary
            income_provenance = "explicit_provided"
        elif ocr_monthly_salary:
            monthly_salary = ocr_monthly_salary
            income_provenance = "ocr_extracted"
        elif content.get("monthly_salary_from_db_estimate"):
            try:
                monthly_salary = float(content.get("monthly_salary_from_db_estimate"))
                income_provenance = "db_estimate"
            except Exception:
                monthly_salary = None
        else:
            monthly_salary = monthly_income_db if monthly_income_db and monthly_income_db > 0 else None
            if monthly_salary:
                income_provenance = "db_derived"

        # Prepare decision object
        decision_obj: Dict[str, Any] = {
            "decision": "rejected",
            "reasons": [],
            "message": "",
            "interest_rate": None,
            "monthly_emi": None,
            "loan_details": None,
            "emi_ratio": None,
            "monthly_salary_used": None,
            "ocr_confidence": float(ocr_conf),
            "ocr_matched_line": ocr_line,
            "ocr_source": ocr_source,
            "requires_income_doc": False,
            "anomalies_detected": [],
            "flag_for_manual_review": False,
        }

        # Evaluate credit score
        if credit_score < 700:
            decision_obj["decision"] = "rejected"
            decision_obj["reasons"].append("credit_score_below_700")
            decision_obj["message"] = f"Rejected: credit score {credit_score} is below threshold 700."
        else:
            # compute EMI for decisioning
            # Choose interest rate depending on whether request > pre_limit
            if loan_amount <= pre_limit:
                annual_rate = 12.0
            else:
                annual_rate = 14.0

            emi_val = self._emi(loan_amount, annual_rate, months)
            emi_ceil = int(ceil(emi_val))

            # Simple caps
            if loan_amount > 2 * pre_limit and pre_limit > 0:
                decision_obj["decision"] = "rejected"
                decision_obj["reasons"].append("amount_exceeds_two_times_preapproved")
                decision_obj["message"] = f"Rejected: requested amount ₹{loan_amount} exceeds allowed upper limit (2x pre-approved ₹{pre_limit})."
            else:
                # If salary needed (request > pre_limit) mark requires_income_doc True when not provided
                if loan_amount > pre_limit:
                    decision_obj["requires_income_doc"] = True

                # If salary available, perform EMI vs salary check
                if monthly_salary and monthly_salary > 0:
                    emi_ratio = emi_val / float(monthly_salary)
                    decision_obj["emi_ratio"] = emi_ratio
                    decision_obj["monthly_salary_used"] = float(monthly_salary)

                    # Approval rule: emi <= 50% monthly salary
                    if emi_val <= 0.5 * monthly_salary:
                        decision_obj["decision"] = "approved"
                        decision_obj["approval_type"] = "income_check_passed"
                        decision_obj["interest_rate"] = float(annual_rate)
                        decision_obj["monthly_emi"] = emi_ceil
                        decision_obj["loan_details"] = {
                            "application_id": f"LOAN{int(time.time()*1000)}",
                            "loan_amount": int(loan_amount),
                            "interest_rate": float(annual_rate),
                            "tenure_months": months,
                            "monthly_emi": emi_ceil,
                            "processing_fee": 5000 if annual_rate > 12 else 0,
                        }
                        decision_obj["message"] = (
                            f"Approved: extracted monthly salary ₹{int(monthly_salary)}; EMI ₹{emi_ceil} is <= 50% of salary (threshold ₹{int(0.5*monthly_salary)})."
                        )
                        
                        # include provenance info
                        decision_obj["income_provenance"] = income_provenance
                        
                        # ============================================
                        # MANUAL REVIEW CHECKS - ADDED HERE
                        # ============================================
                        
                        # Check 1: Salary Mismatch (doc vs DB)
                        try:
                            if monthly_salary and monthly_income_db and monthly_income_db > 0:
                                ratio = max(monthly_salary / monthly_income_db, monthly_income_db / monthly_salary)
                                if ratio >= 3.0:
                                    decision_obj.setdefault("anomalies_detected", [])
                                    decision_obj["anomalies_detected"].append({
                                        "salary_mismatch_detected": {
                                            "doc_salary": float(monthly_salary),
                                            "db_salary": float(monthly_income_db),
                                            "ratio": round(ratio, 2)
                                        }
                                    })
                                    decision_obj["flag_for_manual_review"] = True
                                    decision_obj["message"] += " ⚠️ NOTE: Salary mismatch detected - flagged for manual review."
                        except Exception:
                            pass
                        
                        # Check 2: Low OCR Confidence
                        try:
                            if ocr_conf > 0 and ocr_conf < 0.45:
                                decision_obj.setdefault("anomalies_detected", [])
                                decision_obj["anomalies_detected"].append({"low_ocr_confidence": float(ocr_conf)})
                                decision_obj["flag_for_manual_review"] = True
                                decision_obj["message"] += " ⚠️ NOTE: Low OCR confidence - flagged for manual review."
                        except Exception:
                            pass
                        
                        # ============================================
                        # END MANUAL REVIEW CHECKS
                        # ============================================
                        
                    else:
                        decision_obj["decision"] = "rejected"
                        decision_obj["reasons"].append("emi_exceeds_50_percent_of_salary")
                        decision_obj["interest_rate"] = float(annual_rate)
                        decision_obj["monthly_emi"] = emi_ceil
                        decision_obj["monthly_salary_used"] = float(monthly_salary)
                        decision_obj["message"] = (
                            f"Rejected: EMI ₹{emi_ceil} exceeds 50% of monthly salary ₹{int(monthly_salary)} (threshold ₹{int(0.5*monthly_salary)})."
                        )
                        decision_obj["income_provenance"] = income_provenance
                else:
                    # No salary info
                    if loan_amount <= pre_limit:
                        # approve instantly (shouldn't fall here normally because salary_to_use covers db monthly)
                        decision_obj["decision"] = "approved"
                        decision_obj["approval_type"] = "instant"
                        decision_obj["interest_rate"] = float(12.0)
                        decision_obj["monthly_emi"] = emi_ceil
                        decision_obj["loan_details"] = {
                            "application_id": f"LOAN{int(time.time()*1000)}",
                            "loan_amount": int(loan_amount),
                            "interest_rate": float(12.0),
                            "tenure_months": months,
                            "monthly_emi": emi_ceil,
                            "processing_fee": 0,
                        }
                        decision_obj["message"] = (
                            f"Approved (instant): No income doc required. EMI ₹{emi_ceil}."
                        )
                        decision_obj["income_provenance"] = "instant_no_doc"
                    else:
                        # require salary slip
                        decision_obj["decision"] = "needs_salary_slip"
                        decision_obj["reasons"].append("salary_slip_required")
                        decision_obj["message"] = (
                            "Income document required: please upload salary slip so we can extract monthly salary and re-evaluate EMI vs income."
                        )
                        decision_obj["monthly_emi"] = emi_ceil
                        decision_obj["interest_rate"] = float(annual_rate)
                        decision_obj["income_provenance"] = income_provenance or "missing"

        # Add some helpful debug/tracing fields
        decision_obj["credit_score_used"] = credit_score
        
        # monthly_salary_used was set earlier when salary available; if not, still include DB income
        if decision_obj.get("monthly_salary_used") is None and monthly_income_db and monthly_income_db > 0:
            decision_obj["monthly_salary_used"] = float(monthly_income_db)
            decision_obj.setdefault("income_provenance", "db_derived")

        return AgentMessage(sender=self.agent_id, recipient=message.sender, content=decision_obj)

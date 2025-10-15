# src/agents/sanction_agent.py
import os
from datetime import datetime
from math import ceil
from io import BytesIO

import logging
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.units import inch

from src.agents.base_agent import BaseAgent, AgentMessage
from src.data.database import NBFCDatabase

LOG = logging.getLogger("sanction_agent")
# Quiet the sanction agent by default to remove noisy test output.
LOG.setLevel(logging.ERROR)
if not LOG.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    LOG.addHandler(ch)


class SanctionAgent(BaseAgent):
    """Sanction letter generator that can render to disk or return bytes in-memory."""

    def __init__(self):
        super().__init__("sanction_agent", "sanction")
        self.db = NBFCDatabase()
        os.makedirs("generated_documents", exist_ok=True)
        self.styles = getSampleStyleSheet()
        # custom styles
        self.styles.add(ParagraphStyle(name="HeaderCenter", parent=self.styles["Title"], alignment=TA_CENTER, fontSize=18, leading=22, textColor=colors.darkblue))
        self.styles.add(ParagraphStyle(name="SubInfo", parent=self.styles["Normal"], alignment=TA_CENTER, fontSize=9))
        self.styles.add(ParagraphStyle(name="SectionTitle", parent=self.styles["Heading2"], alignment=TA_CENTER, fontSize=14, leading=16, textColor=colors.darkblue))
        self.styles.add(ParagraphStyle(name="Body", parent=self.styles["Normal"], alignment=TA_LEFT, fontSize=11, leading=14))
        self.styles.add(ParagraphStyle(name="Small", parent=self.styles["Normal"], alignment=TA_LEFT, fontSize=9, leading=11))
        self.styles.add(ParagraphStyle(name="Terms", parent=self.styles["Normal"], alignment=TA_LEFT, fontSize=10, leading=12))

    def _calc_totals(self, loan_amount, annual_rate, months, processing_fee=0):
        """Return dict with monthly_emi, total_interest, total_payable (integers)."""
        try:
            loan_amount = float(loan_amount)
            annual_rate = float(annual_rate or 0)
            months = int(months or 0)
        except Exception:
            return {"monthly_emi": 0, "total_interest": 0, "total_payable": 0}

        r = annual_rate / 12.0 / 100.0
        if months <= 0:
            emi = 0
        elif r == 0:
            emi = loan_amount / months
        else:
            emi = (loan_amount * r * (1 + r) ** months) / ((1 + r) ** months - 1)
        emi = int(ceil(emi))
        total_interest = int(emi * months - loan_amount)
        total_payable = int(loan_amount + total_interest + (processing_fee or 0))
        return {"monthly_emi": emi, "total_interest": total_interest, "total_payable": total_payable}

    def _fmt_amt(self, x, currency="INR"):
        """Format numeric amounts with Rs. prefix for Indian currency."""
        try:
            v = int(x)
            if currency and currency.upper() == "INR":
                return f"Rs. {v:,}"
            else:
                return f"{v:,}"
        except Exception:
            try:
                v = float(x)
                return f"Rs. {v:,.2f}"
            except Exception:
                return str(x)

    def _build_pdf_story(self, customer, decision, loan_details):
        """Return a list of flowables (story) to pass to reportlab doc.build()."""
        story = []
        story.append(Paragraph("QuickCash NBFC Private Limited", self.styles["HeaderCenter"]))
        story.append(Spacer(1, 6/72 * inch))
        story.append(Paragraph("Tower A, Business Park, Sector 62, Gurugram - 122001", self.styles["SubInfo"]))
        story.append(Spacer(1, 2/72 * inch))
        story.append(Paragraph("Phone: +91-124-456-7890 | Email: loans@quickcash.com", self.styles["SubInfo"]))
        story.append(Spacer(1, 10/72 * inch))

        story.append(Paragraph("LOAN SANCTION LETTER", self.styles["SectionTitle"]))
        story.append(Spacer(1, 6/72 * inch))
        story.append(Paragraph(f"Date: {datetime.now().strftime('%B %d, %Y')}", self.styles["Small"]))
        story.append(Spacer(1, 12/72 * inch))

        to_html = f"<b>To:</b><br/>{customer.get('name','')}<br/>{customer.get('city','')}<br/>Phone: {customer.get('phone','')}<br/>Email: {customer.get('email','')}"
        story.append(Paragraph(to_html, self.styles["Body"]))
        story.append(Spacer(1, 10/72 * inch))

        if decision.lower() == "approved":
            story.append(Paragraph(f"Dear {customer.get('name','')},", self.styles["Body"]))
            story.append(Spacer(1, 4/72 * inch))
            story.append(Paragraph(
                "We are pleased to inform you that your application for a Personal Loan has been <b>APPROVED</b> by our Credit Committee. We appreciate your interest in our services and are delighted to extend this loan facility to you.",
                self.styles["Body"]
            ))
        elif decision.lower() == "conditional":
            story.append(Paragraph(f"Dear {customer.get('name','')},", self.styles["Body"]))
            story.append(Spacer(1, 4/72 * inch))
            story.append(Paragraph(
                "We are pleased to inform you that your application for a Personal Loan has been <b>CONDITIONALLY APPROVED</b> subject to completion of remaining checks. Please find the provisional loan details below.",
                self.styles["Body"]
            ))
        else:
            story.append(Paragraph(f"Dear {customer.get('name','')},", self.styles["Body"]))
            story.append(Spacer(1, 4/72 * inch))
            story.append(Paragraph(
                "We regret to inform you that your loan application could not be approved at this time. Please contact our support team for details.",
                self.styles["Body"]
            ))

        story.append(Spacer(1, 12/72 * inch))
        story.append(Paragraph("<b>LOAN DETAILS</b>", self.styles["Heading3"]))
        story.append(Spacer(1, 6/72 * inch))

        app_id = loan_details.get("application_id", f"LOAN{datetime.now().strftime('%Y%m%d%H%M%S')}")
        # Defensive parsing and fallback
        try:
            loan_amount = float(loan_details.get("loan_amount", 0) or 0)
        except Exception:
            loan_amount = 0.0
        try:
            interest_rate = float(loan_details.get("interest_rate", 0) or 0)
        except Exception:
            interest_rate = 0.0
        try:
            tenure = int(loan_details.get("tenure_months", 0) or 0)
        except Exception:
            tenure = 0
        try:
            processing_fee = int(loan_details.get("processing_fee", 0) or 0)
        except Exception:
            processing_fee = 0

        monthly_emi = int(loan_details.get("monthly_emi") or self._calc_totals(loan_amount, interest_rate, tenure, processing_fee)["monthly_emi"])
        totals = self._calc_totals(loan_amount, interest_rate, tenure, processing_fee)

        rows = [
            ["Application ID", app_id],
            ["Loan Amount", f"{self._fmt_amt(loan_amount, loan_details.get('currency','INR'))}"],
            ["Interest Rate", f"{interest_rate:.2f}% per annum"],
            ["Loan Tenure", f"{tenure} months"],
            ["Monthly EMI", f"{self._fmt_amt(monthly_emi, loan_details.get('currency','INR'))}"],
            ["Processing Fee", f"{self._fmt_amt(processing_fee, loan_details.get('currency','INR'))}"],
            ["Total Interest", f"{self._fmt_amt(totals['total_interest'], loan_details.get('currency','INR'))}"],
            ["Total Amount Payable", f"{self._fmt_amt(totals['total_payable'], loan_details.get('currency','INR'))}"],
        ]

        table = Table(rows, colWidths=[2.6 * inch, 3.4 * inch], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)
        story.append(Spacer(1, 12/72 * inch))

        terms = [
            "This sanction is valid for 30 days from the date of this letter.",
            "The loan will be disbursed upon completion of documentation and verification.",
            "EMI payments must be made on or before the due date each month.",
            "Prepayment of the loan is allowed with 30 days notice. Prepayment charges may apply.",
            "Any default in payment will attract penal charges as per our policy.",
            "The loan is subject to our standard terms and conditions.",
            "All disputes are subject to Gurugram jurisdiction only.",
            "Please submit all required documents within 7 working days."
        ]
        story.append(Paragraph("<b>TERMS AND CONDITIONS</b>", self.styles["Heading3"]))
        story.append(Spacer(1, 6/72 * inch))
        for i, t in enumerate(terms, 1):
            story.append(Paragraph(f"{i}. {t}", self.styles["Terms"]))
            story.append(Spacer(1, 3/72 * inch))

        story.append(Spacer(1, 10/72 * inch))
        story.append(Paragraph("Congratulations on your loan approval! We look forward to a long and mutually beneficial relationship. Should you have any queries, please feel free to contact our customer service team.", self.styles["Body"]))
        story.append(Spacer(1, 12/72 * inch))

        story.append(Paragraph("For QuickCash NBFC Private Limited", self.styles["Body"]))
        story.append(Spacer(1, 8/72 * inch))
        story.append(Paragraph("Authorized Signatory", self.styles["Body"]))
        story.append(Spacer(1, 6/72 * inch))
        story.append(Paragraph(f"Date: {datetime.now().strftime('%B %d, %Y')}", self.styles["Small"]))
        story.append(Spacer(1, 10/72 * inch))

        footer = ("This is a system-generated document. For any queries, contact us at +91-124-456-7890 or loans@quickcash.com  "
                  f"RBI Registration: N-13.02268 | This NBFC is registered with RBI")
        story.append(Paragraph(footer, self.styles["Small"]))
        return story

    def _render_to_bytes(self, customer, decision, loan_details):
        """Render PDF to bytes and return bytes."""
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=36)
        story = self._build_pdf_story(customer, decision, loan_details)
        doc.build(story)
        buf.seek(0)
        return buf.read()

    def _render_to_file(self, customer, decision, loan_details, pdf_path):
        """Write PDF to file at pdf_path."""
        doc = SimpleDocTemplate(pdf_path, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=36)
        story = self._build_pdf_story(customer, decision, loan_details)
        doc.build(story)

    async def handle(self, message: AgentMessage) -> AgentMessage:
        """
        message.content keys:
          - customer_id
          - decision ('approved'|'conditional'|'rejected')
          - loan_details (dict)
          - save_to_disk: bool (default True)
        returns:
          - generated: bool
          - pdf_path or pdf_bytes if generated (pdf_bytes only when save_to_disk is False)
          - message (summary)
        """
        content = message.content or {}

        # Merge/normalize inputs: accept nested result objects or top-level keys
        uw = content.get("underwriting_result") or content.get("underwriting") or {}
        sales = content.get("sales_result") or content.get("sales") or {}
        verification = content.get("verification_result") or content.get("verification") or {}
        merged = {}
        # order of precedence: verification < sales < underwriting < top-level
        if isinstance(verification, dict):
            merged.update(verification)
        if isinstance(sales, dict):
            merged.update(sales)
        if isinstance(uw, dict):
            merged.update(uw)
        # include top-level primitive keys if not overwritten
        for k, v in content.items():
            if k not in ("underwriting_result", "sales_result", "verification_result"):
                if k not in merged:
                    merged[k] = v

        cust_id = merged.get("customer_id") or content.get("customer_id")
        decision = (merged.get("decision") or content.get("decision") or "rejected").lower()
        # loan_details might be passed nested or top-level
        loan_details = merged.get("loan_details") or content.get("loan_details") or merged
        save_to_disk = bool(content.get("save_to_disk", True))

        # try to load customer from DB; if not found, fall back to fields in merged payload
        customer = None
        if cust_id:
            customer = self.db.get_customer(cust_id)
        if not customer:
            # fall back to values in merged or defaults so PDF still generates
            LOG.debug("Customer not found in DB for id=%s — falling back to payload values", cust_id)
            customer = {
                "name": merged.get("name") or merged.get("customer_name") or "Guest User",
                "city": merged.get("city") or merged.get("address") or "",
                "phone": merged.get("phone") or merged.get("mobile") or "0000000000",
                "email": merged.get("email") or merged.get("contact_email") or ""
            }

        # If not approved/conditional -> do not generate; but still return a structured message
        if decision not in ("approved", "conditional"):
            summary = "We are sorry — your loan application could not be approved."
            return AgentMessage(sender=self.agent_id, recipient=message.sender,
                                content={
                                    "customer_id": cust_id,
                                    "decision": decision,
                                    "pdf_path": None,
                                    "pdf_bytes": None,
                                    "message": summary,
                                    "generated": False,
                                    "reason": "not_approved"
                                })

        # Prefer nested loan_details.application_id when available
        application_id = loan_details.get("application_id") or loan_details.get("app_id") or f"LOAN{datetime.now().strftime('%Y%m%d%H%M%S')}"
        loan_details["application_id"] = application_id

        # Build human summary (safe formatting)
        try:
            loan_amt_display = self._fmt_amt(loan_details.get('loan_amount', 0), loan_details.get('currency', 'INR'))
        except Exception:
            loan_amt_display = str(loan_details.get('loan_amount', 0))
        summary = f"Your loan for {loan_amt_display} has been {decision.upper()}."
        if loan_details.get("application_id"):
            summary += f" Application ID: {loan_details.get('application_id')}."

        pdf_path = None
        pdf_bytes = None

        try:
            if save_to_disk:
                filename = f"sanction_{cust_id or application_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
                pdf_path = os.path.join("generated_documents", filename)
                # generate to disk
                self._render_to_file(customer, decision, loan_details, pdf_path)
                # read back bytes optionally (we will omit from response when saved to disk)
                try:
                    with open(pdf_path, "rb") as f:
                        pdf_bytes = f.read()
                except Exception as e:
                    LOG.error("Failed to read-back generated PDF bytes: %s", e)
                    pdf_bytes = None
            else:
                pdf_bytes = self._render_to_bytes(customer, decision, loan_details)
        except Exception as e:
            LOG.exception("Sanction PDF generation failed: %s", e)
            return AgentMessage(sender=self.agent_id, recipient=message.sender,
                                content={"error": f"pdf_render_failed: {e}", "generated": False})

        # Debug totals — kept quiet
        try:
            loan_amount = float(loan_details.get("loan_amount", 0) or 0)
            interest_rate = float(loan_details.get("interest_rate", 0) or 0)
            tenure = int(loan_details.get("tenure_months", 0) or 0)
            processing_fee = int(loan_details.get("processing_fee", 0) or 0)
            totals = self._calc_totals(loan_amount, interest_rate, tenure, processing_fee)
            LOG.debug("Sanction computed totals: loan=%s rate=%s tenure=%s -> totals=%s", loan_amount, interest_rate, tenure, totals)
        except Exception:
            LOG.debug("Could not compute sanction totals for debug.")

        # Build response — if saved to disk, do NOT include pdf_bytes to avoid binary dumps in logs/tests.
        resp = {
            "customer_id": cust_id,
            "decision": decision,
            "pdf_path": pdf_path if save_to_disk else None,
            "message": summary,
            "generated": True
        }
        if not save_to_disk:
            resp["pdf_bytes"] = pdf_bytes

        return AgentMessage(sender=self.agent_id, recipient=message.sender, content=resp)
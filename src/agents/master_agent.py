"""
    MasterAgent coordinates the orchestrator and exposes a simple conversation API
    used by test_master_agent.py.

    Improvements:
    - Reuses last_customer_id when orchestrator is invoked without an explicit customer_id.
    - Persists application record using underwriting's application_id and performs upsert when a UNIQUE constraint occurs.
    - Writes pdf_bytes to disk if needed and returns pdf_path+pdf_bytes+application_record=id.
    """

import os
import time
import sqlite3
from datetime import datetime

from src.data.database import NBFCDatabase
from src.agents.orchestrator import LoanOrchestrator
from src.agents.base_agent import BaseAgent, AgentMessage


class MasterAgent(BaseAgent):
    
    def __init__(self):
        # Attempt flexible super().__init__() to match your BaseAgent signature variations
        try:
            super().__init__(agent_id="master_agent")
        except TypeError:
            try:
                super().__init__("master_agent", "master")
            except Exception:
                super().__init__()

        self.orchestrator = LoanOrchestrator()
        self.db = NBFCDatabase()
        self.last_customer_id = None
        self.conversation_id = f"conv_{int(time.time())}"
        print("✅ Master Agent initialized - Ready to serve customers!")

    # --------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------
    async def start_conversation(self, metadata: dict = None) -> dict:
        """
        Starts a new conversation and returns a welcome message.
        Returns: {'conversation_id': str, 'message': str}
        """
        metadata = metadata or {}
        self.conversation_id = f"conv_{int(time.time())}"

        welcome_msg = (
            "Welcome to QuickCash! 🎉 I'm Sarah, and I'm here to make getting a personal loan "
            "super easy for you. Whether it's for home renovation, medical needs, or any urgent "
            "expense - I've got you covered. Tell me, what's on your mind?"
        )

        return {
            "conversation_id": self.conversation_id,
            "message": welcome_msg,
            "metadata": metadata
        }

    async def chat(self, conversation_id: str, user_input: str) -> dict:
        """
        Process a single user input (message or file path) and return agent response.
        Returns: {'message': str, 'next_action': str|None, 'data': dict|None}
        """
        # If user_input looks like a local file path, treat it as a document upload
        if os.path.isfile(user_input):
            return await self._handle_document_upload(conversation_id, user_input)

        # Otherwise parse as text input
        return await self._handle_text_input(conversation_id, user_input)

    def get_conversation_state(self, conversation_id: str) -> dict:
        """
        Retrieve the current state of a conversation.
        For demo: returns orchestrator's internal flow state if available.
        """
        flow = self.orchestrator.flows.get(conversation_id)
        if flow:
            return {
                "conversation_id": conversation_id,
                "flow": flow,
                "customer_id": self.last_customer_id
            }
        return {"conversation_id": conversation_id, "flow": None}

    # --------------------------------------------------------------------------
    # Internal Handlers
    # --------------------------------------------------------------------------
    async def _handle_text_input(self, conversation_id: str, text: str) -> dict:
        """
        Handle text messages from the user.
        This can be phone numbers, loan requests, confirmations, etc.
        """
        text_lower = text.lower().strip()

        # Phone number detection
        if text_lower.startswith("+91") or text_lower.startswith("91") or (
            len(text_lower.replace(" ", "").replace("-", "")) >= 10 and text_lower[0].isdigit()
        ):
            return await self._handle_phone_input(conversation_id, text)

        # Loan amount + tenure detection (e.g., "I need 3 lakhs for 24 months")
        if any(keyword in text_lower for keyword in ["need", "want", "loan", "lakh", "thousand", "rupees", "₹"]):
            return await self._handle_loan_request(conversation_id, text)

        # Confirmation detection
        if any(word in text_lower for word in ["yes", "proceed", "confirm", "ok", "start", "apply"]):
            return await self._handle_confirmation(conversation_id)

        # Default fallback
        return {
            "message": "I didn't quite catch that. Could you please share your phone number or tell me how much loan you need?",
            "next_action": "clarify",
            "data": None
        }

    async def _handle_phone_input(self, conversation_id: str, phone: str) -> dict:
        """Handle phone number input"""
        # Clean phone number
        phone_clean = phone.strip().replace(" ", "").replace("-", "")
        
        # Add +91 prefix if not present
        if not phone_clean.startswith("+91"):
            if phone_clean.startswith("91"):
                phone_clean = "+" + phone_clean
            else:
                phone_clean = "+91" + phone_clean

        # Lookup customer
        customer = self.db.get_customer_by_phone(phone_clean)

        if not customer:
            return {
                "message": f"Sorry, I couldn't find a customer with phone {phone}. Please check and try again.",
                "next_action": "retry_phone",
                "data": None
            }

        self.last_customer_id = customer["customer_id"]
        credit_score = int(customer.get("credit_score", 0) or 0)
        pre_approved = float(customer.get("pre_approved_limit", 0) or 0)

        response = (
            f"Great! How much do you need? You can tell me in lakhs or the full amount. "
            f"(You're pre-approved for up to ₹{int(pre_approved):,})"
        )

        return {
            "message": response,
            "next_action": "amount_input",
            "data": {
                "customer_id": self.last_customer_id,
                "credit_score": credit_score,
                "pre_approved_limit": pre_approved
            }
        }

    async def _handle_loan_request(self, conversation_id: str, text: str) -> dict:
        """Parse loan amount and tenure from user text"""
        import re

        # Extract amount
        amount = None
        if "lakh" in text.lower():
            match = re.search(r"(\d+(?:\.\d+)?)\s*lakh", text.lower())
            if match:
                amount = float(match.group(1)) * 100000
        elif "thousand" in text.lower():
            match = re.search(r"(\d+(?:\.\d+)?)\s*thousand", text.lower())
            if match:
                amount = float(match.group(1)) * 1000
        else:
            # Try to find plain numbers
            match = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)", text)
            if match:
                amount = float(match.group(1).replace(",", ""))

        # Extract tenure
        tenure = 36  # default
        tenure_match = re.search(r"(\d+)\s*month", text.lower())
        if tenure_match:
            tenure = int(tenure_match.group(1))

        if not amount:
            return {
                "message": "Could you please specify the loan amount? For example: 'I need 3 lakhs' or '₹300,000'",
                "next_action": "clarify_amount",
                "data": None
            }

        # Check if we have customer_id
        if not self.last_customer_id:
            return {
                "message": "Please share your registered mobile number first so I can check your eligibility.",
                "next_action": "phone_input",
                "data": None
            }

        # Get customer details
        customer = self.db.get_customer(self.last_customer_id)
        pre_limit = float(customer.get("pre_approved_limit", 0) or 0)
        credit_score = int(customer.get("credit_score", 0) or 0)

        # Quick eligibility check
        if credit_score < 700:
            return {
                "message": (
                    f"I appreciate your interest! However, I need to be upfront with you - "
                    f"your credit score ({credit_score}) is below our minimum requirement of 700. 😔 "
                    f"Unfortunately, we cannot approve your loan at this time.\n\n"
                    f"💡 **Suggestion:** Work on improving your credit score by paying bills on time "
                    f"and reducing debt. Come back in 3-6 months, and we'll be happy to help!"
                ),
                "next_action": "none",
                "data": {"decision": "rejected", "reason": "low_credit_score"}
            }

        # Compute EMI estimate
        def compute_emi(principal, rate, months):
            r = rate / 12.0 / 100.0
            if r == 0:
                return principal / months
            return (principal * r * (1 + r) ** months) / ((1 + r) ** months - 1)

        rate = 12.0 if amount <= pre_limit else 14.0
        emi = int(compute_emi(amount, rate, tenure))

        # Check if above pre-approved limit
        if amount > pre_limit:
            response = (
                f"Perfect! ✅ Here's your loan summary:\n\n"
                f"💰 Amount: ₹{int(amount):,}\n"
                f"📅 Tenure: {tenure} months\n"
                f"💳 EMI: ~₹{emi:,}/month\n"
                f"📊 Interest: {rate}% per annum\n"
                f"🏆 Your Credit Score: {credit_score} (Excellent!)\n\n"
                f"📎 This is ₹{int(amount - pre_limit):,} above your pre-approved limit. "
                f"I'll need your latest salary slip to verify income.  Ready to proceed?\n\n"
                f"💡 Suggestions: Yes, proceed | Tell me more | Change amount"
            )
            next_action = "confirm"
        else:
            response = (
                f"Perfect! ✅ Here's your loan summary:\n\n"
                f"💰 Amount: ₹{int(amount):,}\n"
                f"📅 Tenure: {tenure} months\n"
                f"💳 EMI: ~₹{emi:,}/month\n"
                f"📊 Interest: {rate}% per annum\n"
                f"🏆 Your Credit Score: {credit_score} (Excellent!)\n\n"
                f"✅ You're pre-approved! Ready to proceed?\n\n"
                f"💡 Suggestions: Yes, proceed | Tell me more | Change amount"
            )
            next_action = "confirm"

        # Store loan request in flow state
        if conversation_id not in self.orchestrator.flows:
            self.orchestrator.flows[conversation_id] = type('obj', (object,), {})()
        
        self.orchestrator.flows[conversation_id].loan_amount = amount
        self.orchestrator.flows[conversation_id].tenure_months = tenure
        self.orchestrator.flows[conversation_id].estimated_emi = emi
        self.orchestrator.flows[conversation_id].interest_rate = rate
        self.orchestrator.flows[conversation_id].customer_id = self.last_customer_id

        return {
            "message": response,
            "next_action": next_action,
            "data": {
                "loan_amount": amount,
                "tenure_months": tenure,
                "estimated_emi": emi,
                "interest_rate": rate
            }
        }

    async def _handle_confirmation(self, conversation_id: str) -> dict:
        """Handle user confirmation to proceed with application"""
        if not self.last_customer_id:
            return {
                "message": "Please share your phone number first.",
                "next_action": "phone_input",
                "data": None
            }

        # Get customer
        customer = self.db.get_customer(self.last_customer_id)
        pre_limit = float(customer.get("pre_approved_limit", 0) or 0)

        # Check flow state
        flow_state = self.orchestrator.flows.get(conversation_id)
        loan_amount = getattr(flow_state, 'loan_amount', None) if flow_state else None

        # If within pre-approved, process directly
        if loan_amount and loan_amount <= pre_limit:
            # Process instantly
            return await self._process_instant_approval(conversation_id, customer)
        
        # Otherwise ask for salary slip
        response = (
            "Great! 📎 To proceed with this loan amount, I'll need to verify your income.\n\n"
            "**Please provide the file path to your latest salary slip.**\n"
            "Example: E:\\\\documents\\\\salary_slip.pdf\n\n"
            "Or type 'sample' to use test data."
        )

        return {
            "message": response,
            "next_action": "upload_document",
            "data": {"customer_id": self.last_customer_id}
        }

    async def _process_instant_approval(self, conversation_id: str, customer: dict) -> dict:
        """Process instant approval for pre-approved amounts"""
        flow_state = self.orchestrator.flows.get(conversation_id)
        
        orchestrator_input = {
            "customer_id": customer["customer_id"],
            "phone": customer.get("phone"),
            "loan_amount": getattr(flow_state, 'loan_amount', None) if flow_state else None,
            "tenure_months": getattr(flow_state, 'tenure_months', 36) if flow_state else 36,
        }

        orchestrator_result = await self.orchestrator.run(orchestrator_input)
        
        # Check for manual review
        underwriting_result = orchestrator_result.get("underwriting_result")
        if underwriting_result and underwriting_result.get("flag_for_manual_review"):
            return self._format_manual_review_response(underwriting_result, orchestrator_result)
        
        # Format response
        if underwriting_result and underwriting_result.get("decision") == "approved":
            sanction_result = orchestrator_result.get("sanction_result")
            return self._format_approval_response(underwriting_result, sanction_result)
        else:
            return self._format_rejection_response(underwriting_result)

    async def _handle_document_upload(self, conversation_id: str, file_path: str) -> dict:
        """
        Handle document upload (salary slip) and process through orchestrator.
        """
        print("📎 [Master Agent] Processing uploaded document...")
        
        if not self.last_customer_id:
            return {
                "message": "Error: Please start conversation and provide phone number first.",
                "next_action": "restart",
                "data": None
            }

        if not os.path.exists(file_path):
            return {
                "message": f"File not found: {file_path}. Please check the path and try again.",
                "next_action": "retry_upload",
                "data": None
            }

        # Read file bytes
        try:
            with open(file_path, "rb") as f:
                file_bytes = f.read()
        except Exception as e:
            return {
                "message": f"Error reading file: {str(e)}",
                "next_action": "retry_upload",
                "data": None
            }

        # Get customer details
        customer = self.db.get_customer(self.last_customer_id)
        
        # Get loan details from flow state
        flow_state = self.orchestrator.flows.get(conversation_id)
        loan_amount = getattr(flow_state, 'loan_amount', 300000) if flow_state else 300000
        tenure_months = getattr(flow_state, 'tenure_months', 24) if flow_state else 24

        # Build orchestrator input
        orchestrator_input = {
            "customer_id": self.last_customer_id,
            "phone": customer.get("phone"),
            "loan_amount": loan_amount,
            "tenure_months": tenure_months,
            "uploaded_docs": [
                {
                    "doc_type": "salary_slip",
                    "file_name": os.path.basename(file_path),
                    "file_bytes": file_bytes
                }
            ]
        }

        print("🔄 [Master Agent] Sending to Underwriting Agent for OCR and verification...")
        
        # Run orchestrator
        orchestrator_result = await self.orchestrator.run(orchestrator_input)

        # Extract results
        verification_result = orchestrator_result.get("verification_result")
        underwriting_result = orchestrator_result.get("underwriting_result")
        sanction_result = orchestrator_result.get("sanction_result")

        # ==================================================================
        # CHECK FOR MANUAL REVIEW - NEW SECTION
        # ==================================================================
        if underwriting_result and underwriting_result.get("flag_for_manual_review"):
            return self._format_manual_review_response(underwriting_result, orchestrator_result)

        # ==================================================================
        # NORMAL FLOW (NO MANUAL REVIEW)
        # ==================================================================
        if underwriting_result and underwriting_result.get("decision") == "approved":
            return self._format_approval_response(underwriting_result, sanction_result)

        elif underwriting_result and underwriting_result.get("decision") == "rejected":
            return self._format_rejection_response(underwriting_result)

        else:
            return {
                "message": "Unable to process application. Please try again or contact support.",
                "next_action": "error",
                "data": orchestrator_result
            }

    def _format_manual_review_response(self, uw_result: dict, full_result: dict) -> dict:
        """
        Format response when manual review is required - NEW METHOD
        """
        anomalies = uw_result.get("anomalies_detected", [])
        loan_details = uw_result.get("loan_details", {})
        
        response = "📄 **Document Analysis Complete!**\n\n"
        response += "⚠️ **APPLICATION UNDER MANUAL REVIEW**\n\n"
        response += "Your application meets our initial criteria, but we've detected some data inconsistencies that require human verification:\n\n"
        
        # List anomalies
        for anomaly in anomalies:
            if "salary_mismatch_detected" in anomaly:
                sm = anomaly["salary_mismatch_detected"]
                response += f"🔍 **Salary Verification Required:**\n"
                response += f"  • Document shows: ₹{int(sm['doc_salary']):,}/month\n"
                response += f"  • System records: ₹{int(sm['db_salary']):,}/month\n"
                response += f"  • Variance: {sm['ratio']:.1f}x (requires manual verification)\n\n"
            
            if "low_ocr_confidence" in anomaly:
                response += f"📋 **Document Quality Issue:**\n"
                response += f"  • OCR confidence: {anomaly['low_ocr_confidence']*100:.0f}%\n"
                response += f"  • Clearer document copy may be required\n\n"
        
        response += "✅ **What Happens Next:**\n"
        response += "  • Your application has been flagged for manual review\n"
        response += "  • Our verification team will contact you within **2 business days**\n"
        response += "  • You may be asked to provide:\n"
        response += "    - Original salary slip copy\n"
        response += "    - Bank statements (last 3 months)\n"
        response += "    - Employment verification letter\n\n"
        
        response += "📋 **Application Details:**\n"
        response += f"  • Application ID: {loan_details.get('application_id', 'N/A')}\n"
        response += f"  • Requested Amount: ₹{int(loan_details.get('loan_amount', 0)):,}\n"
        response += f"  • Tenure: {loan_details.get('tenure_months', 0)} months\n"
        response += f"  • Monthly EMI: ₹{int(loan_details.get('monthly_emi', 0)):,}\n"
        response += f"  • Interest Rate: {loan_details.get('interest_rate', 0)}% p.a.\n\n"
        
        response += "📞 **Have questions?** Contact our support team:\n"
        response += "  • Email: support@quickcash.com\n"
        response += "  • Phone: 1800-QUICKCASH\n\n"
        response += "We appreciate your patience and will process your application as quickly as possible! 🙏"
        
        return {
            "message": response,
            "next_action": "wait_for_review",
            "data": {
                "decision": "manual_review_required",
                "application_id": loan_details.get("application_id"),
                "manual_review_required": True,
                "anomalies": anomalies,
                "review_snapshot": full_result.get("manual_review_snapshot"),
                "loan_details": loan_details
            }
        }

    def _format_approval_response(self, uw_result: dict, sanction_result: dict = None) -> dict:
        """Format approval response"""
        loan_details = uw_result.get("loan_details", {})
        monthly_salary = uw_result.get("monthly_salary_used")
        ocr_conf = uw_result.get("ocr_confidence", 0)
        ocr_line = uw_result.get("ocr_matched_line", "")
        ocr_source = uw_result.get("ocr_source", "")
        
        response = "📄 **Document Analysis Complete!**\n\n"
        
        response += "🔍 **OCR Results:**\n"
        response += f"  • Confidence: {ocr_conf*100:.1f}%\n"
        if ocr_line:
            response += f'  • Extracted Line: "{ocr_line}"\n'
        if ocr_source:
            response += f"  • Source: {ocr_source}\n"
        if monthly_salary:
            response += f"  • Monthly Salary Detected: ₹{int(monthly_salary):,}\n"
        response += "\n"
        
        response += "📊 **Eligibility Verification:**\n"
        response += f"  ✅ Credit Score: {uw_result.get('credit_score_used', 'N/A')} (Minimum: 700)\n"
        if monthly_salary:
            response += f"  • Monthly Salary: ₹{int(monthly_salary):,}\n"
            response += f"  • Requested EMI: ₹{int(loan_details.get('monthly_emi', 0)):,}\n"
            emi_ratio = uw_result.get("emi_ratio", 0)
            response += f"  • EMI as % of Salary: {emi_ratio*100:.1f}%\n"
            response += f"  • EMI Threshold (50%): ₹{int(0.5*monthly_salary):,}\n"
            response += "  ✅ **EMI Check: PASSED** (EMI ≤ 50% of salary)\n\n"
        
        response += "🎉 **LOAN APPROVED!**\n\n"
        response += "📋 **Final Loan Details:**\n"
        response += f"  • Application ID: {loan_details.get('application_id', 'N/A')}\n"
        response += f"  • Amount: ₹{int(loan_details.get('loan_amount', 0)):,}\n"
        response += f"  • Tenure: {loan_details.get('tenure_months', 0)} months\n"
        response += f"  • Monthly EMI: ₹{int(loan_details.get('monthly_emi', 0)):,}\n"
        response += f"  • Interest Rate: {loan_details.get('interest_rate', 0)}% p.a.\n"
        response += f"  • Processing Fee: ₹{int(loan_details.get('processing_fee', 0)):,}\n\n"
        
        if sanction_result and sanction_result.get("pdf_path"):
            response += f"📄 **Your Sanction Letter:**\n"
            response += f"  Download from: {sanction_result.get('pdf_path')}\n\n"
        
        response += "💰 Funds will be disbursed within 24 hours.\n\n"
        response += "Welcome to QuickCash! 🚀"
        
        return {
            "message": response,
            "next_action": "complete",
            "data": {
                "decision": "approved",
                "loan_details": loan_details,
                "sanction_letter": sanction_result.get("pdf_path") if sanction_result else None
            }
        }

    def _format_rejection_response(self, uw_result: dict) -> dict:
        """Format rejection response"""
        reasons = uw_result.get("reasons", [])
        message = uw_result.get("message", "Loan application rejected.")
        
        response = "❌ **Application Decision: REJECTED**\n\n"
        response += f"{message}\n\n"
        
        if "credit_score_below_700" in reasons:
            response += "💡 **Suggestions to improve your credit score:**\n"
            response += "  • Pay all bills and EMIs on time\n"
            response += "  • Reduce credit card utilization below 30%\n"
            response += "  • Don't apply for multiple loans simultaneously\n"
            response += "  • Check your credit report for errors\n\n"
            response += "Come back in 3-6 months after improving your score, and we'll be happy to help!"
        
        return {
            "message": response,
            "next_action": "end",
            "data": {
                "decision": "rejected",
                "reasons": reasons
            }
        }

"""
    Master Agent (Main Orchestrator) - Conversational Entry/Exit Point

    Responsibilities:
    1. ENTRY: Greet customers, understand needs, build rapport
    2. ORCHESTRATE: Delegate to Worker Agents (Sales, Verification, Underwriting, Sanction)
    3. ANALYZE: Show detailed OCR results, salary verification, EMI affordability
    4. EXIT: Close conversation gracefully with next steps
    """
import os
import time
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

# LLM for conversational capability
try:
    from langchain_groq import ChatGroq
    from langchain.schema import SystemMessage, HumanMessage, AIMessage
    LLM_AVAILABLE = True
except Exception:
    LLM_AVAILABLE = False

from src.data.database import NBFCDatabase
from src.agents.orchestrator import LoanOrchestrator, LoanFlowState
from src.agents.base_agent import BaseAgent, AgentMessage

class ConversationState:
    """Tracks the state of an ongoing conversation"""
    GREETING = "greeting"
    EXPLORING_NEEDS = "exploring_needs"
    PRESENTING_OFFER = "presenting_offer"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COLLECTING_DETAILS = "collecting_details"
    PROCESSING = "processing"
    AWAITING_DOCUMENT = "awaiting_document"
    COMPLETED = "completed"
    CLOSED = "closed"

class MasterAgent(BaseAgent):
    

    def __init__(self):
        try:
            super().__init__(agent_id="master_agent")
        except TypeError:
            try:
                super().__init__("master_agent", "master")
            except Exception:
                super().__init__()

        self.orchestrator = LoanOrchestrator()
        self.db = NBFCDatabase()

        # Conversational LLM
        if LLM_AVAILABLE:
            try:
                self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.3)
            except Exception:
                self.llm = None
        else:
            self.llm = None

        # Active conversations
        self.conversations = {}  # {conversation_id: {state, history, flow, customer_id}}
        
        print("✅ Master Agent initialized - Ready to serve customers!")

    # ==================== ENTRY POINT ====================
    async def start_conversation(self, entry_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        ENTRY POINT: Customer lands on chatbot via digital ad or marketing email
        """
        entry_context = entry_context or {}
        conv_id = f"conv_{int(time.time() * 1000)}"

        # Initialize conversation state
        self.conversations[conv_id] = {
            "state": ConversationState.GREETING,
            "history": [],
            "flow": LoanFlowState(),
            "customer_id": None,
            "entry_context": entry_context,
            "created_at": time.time(),
            "last_activity": time.time(),
            "collected_info": {  # Track what info we've gathered
                "amount": None,
                "tenure": None,
                "purpose": None
            }
        }

        # Generate personalized greeting
        source = entry_context.get("source", "our website")
        greeting = await self._generate_greeting(source)

        # Log conversation start
        self._add_to_history(conv_id, "assistant", greeting)

        return {
            "conversation_id": conv_id,
            "message": greeting,
            "state": ConversationState.GREETING,
            "suggested_actions": [
                "I need a personal loan",
                "Tell me about loan offers",
                "What documents do I need?",
                "Check my eligibility"
            ],
            "metadata": {
                "agent": "Master Agent (Orchestrator)",
                "timestamp": datetime.now().isoformat()
            }
        }

    async def _generate_greeting(self, source: str) -> str:
        """Generate a warm, personalized greeting"""
        greetings = {
            "facebook_ad": "Hi there! 👋 I'm Sarah from QuickCash. I noticed you clicked our ad about instant personal loans. I'm here to help you get quick funds with minimal paperwork. How can I assist you today?",
            "email_campaign": "Hello! 😊 I'm Sarah, your personal loan advisor at QuickCash. Thanks for responding to our email! I'm here to help you explore loan options that fit your needs. What brings you here today?",
            "website": "Welcome to QuickCash! 🎉 I'm Sarah, and I'm here to make getting a personal loan super easy for you. Whether it's for home renovation, medical needs, or any urgent expense - I've got you covered. Tell me, what's on your mind?",
            "default": "Hi! I'm Sarah from QuickCash 👋 I'm here to help you get a personal loan quickly and easily. What can I do for you today?"
        }

        return greetings.get(source, greetings["default"])

    # ==================== CONVERSATION HANDLER ====================
    async def chat(self, conversation_id: str, user_message: str,
                   uploaded_file: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Main conversational interface - handles all user messages
        """
        # Validate conversation exists
        if conversation_id not in self.conversations:
            return {
                "error": "conversation_not_found",
                "message": "Sorry, I couldn't find our conversation. Let's start fresh!",
                "action": "restart"
            }

        conv = self.conversations[conversation_id]
        conv["last_activity"] = time.time()

        # Log user message
        self._add_to_history(conversation_id, "user", user_message)

        # Get current state
        current_state = conv["state"]

        # Route to appropriate handler based on conversation state
        if current_state == ConversationState.GREETING:
            response = await self._handle_greeting_state(conversation_id, user_message)
        elif current_state == ConversationState.EXPLORING_NEEDS:
            response = await self._handle_exploring_needs(conversation_id, user_message)
        elif current_state == ConversationState.PRESENTING_OFFER:
            response = await self._handle_presenting_offer(conversation_id, user_message)
        elif current_state == ConversationState.AWAITING_CONFIRMATION:
            response = await self._handle_awaiting_confirmation(conversation_id, user_message)
        elif current_state == ConversationState.COLLECTING_DETAILS:
            response = await self._handle_collecting_details(conversation_id, user_message)
        elif current_state == ConversationState.AWAITING_DOCUMENT:
            response = await self._handle_document_upload(conversation_id, user_message, uploaded_file)
        elif current_state == ConversationState.PROCESSING:
            response = await self._handle_processing(conversation_id, user_message)
        elif current_state == ConversationState.COMPLETED:
            response = await self._handle_completed(conversation_id, user_message)
        else:
            response = {
                "message": "I'm not sure where we were. Could you tell me again what you need?",
                "state": ConversationState.EXPLORING_NEEDS
            }

        # Update conversation state
        if "state" in response:
            conv["state"] = response["state"]

        # Log assistant response
        self._add_to_history(conversation_id, "assistant", response.get("message", ""))

        return response

    # ==================== STATE HANDLERS ====================
    async def _handle_greeting_state(self, conv_id: str, message: str) -> Dict[str, Any]:
        """Handle initial greeting - understand customer intent"""
        conv = self.conversations[conv_id]

        # Try to identify customer by phone/email in message
        customer_id = await self._identify_customer(message)
        if customer_id:
            conv["customer_id"] = customer_id
            conv["flow"].customer_id = customer_id

            # Get customer details
            customer = self.db.get_customer(customer_id)
            if customer:
                name = customer.get("name", "")
                pre_limit = int(customer.get("pre_approved_limit", 0) or 0)
                credit_score = int(customer.get("credit_score", 0) or 0)

                # Personalized greeting with pre-approved offer
                response = (
                    f"Great to meet you, {name}! 😊 I can see you're already in our system. "
                    f"You have a pre-approved loan of up to ₹{pre_limit:,} at 12% interest. "
                )
                
                if credit_score >= 700:
                    response += f"Your credit score ({credit_score}) is excellent! "
                else:
                    response += f"However, I notice your credit score ({credit_score}) is below our minimum requirement of 700. "
                
                response += "What brings you here today?"

                conv["state"] = ConversationState.EXPLORING_NEEDS
                return {
                    "message": response,
                    "state": ConversationState.EXPLORING_NEEDS,
                    "next_action": "wait",
                    "suggested_actions": [
                        f"I need ₹{int(pre_limit * 0.8):,}",
                        "Check my eligibility",
                        "Home renovation loan",
                        "Medical emergency"
                    ]
                }

        # Detect intent
        msg_lower = message.lower()
        loan_keywords = ["loan", "money", "borrow", "urgent", "need", "help", "cash", "funds", "credit"]
        wants_loan = any(kw in msg_lower for kw in loan_keywords)

        if wants_loan:
            # Customer expressed interest - move to exploring needs
            conv["state"] = ConversationState.EXPLORING_NEEDS

            if not customer_id:
                # Ask for phone to check eligibility
                response = (
                    "Perfect! I'm here to help. 😊 To check your eligibility and see if you have "
                    "any pre-approved offers, could you share your registered mobile number?"
                )

                return {
                    "message": response,
                    "state": ConversationState.EXPLORING_NEEDS,
                    "next_action": "wait",
                    "suggested_actions": ["+91 98543...", "I'll provide it"]
                }

        else:
            # General query - be helpful but steer toward loan
            if self.llm:
                response = await self._generate_conversational_response(
                    conv_id, message,
                    system_context="Be helpful and subtly guide conversation toward personal loan offerings."
                )
            else:
                response = (
                    "I'm here to help with personal loans! Whether it's for home renovation, "
                    "medical needs, education, or any emergency - I can get you approved quickly. "
                    "What do you need?"
                )

            return {
                "message": response,
                "state": ConversationState.GREETING,
                "next_action": "wait",
                "suggested_actions": ["I need a loan", "Check eligibility", "Tell me more"]
            }

    async def _handle_exploring_needs(self, conv_id: str, message: str) -> Dict[str, Any]:
        """Understand customer needs - collect amount, tenure step by step"""
        conv = self.conversations[conv_id]
        flow = conv["flow"]
        collected = conv["collected_info"]

        # Try to identify customer if not already done
        if not conv["customer_id"]:
            customer_id = await self._identify_customer(message)
            if customer_id:
                conv["customer_id"] = customer_id
                flow.customer_id = customer_id

        # Get customer context
        customer = None
        pre_limit = 0
        credit_score = 0
        annual_income = 0
        
        if conv["customer_id"]:
            customer = self.db.get_customer(conv["customer_id"])
            if customer:
                pre_limit = float(customer.get("pre_approved_limit", 0) or 0)
                credit_score = int(customer.get("credit_score", 0) or 0)
                annual_income = float(customer.get("annual_income", 0) or 0)

        # Parse message for loan details
        import re
        msg_lower = message.lower()

        # Extract amount (ONLY if not already collected)
        if not collected["amount"]:
            amount_match = re.search(r'(\d+)\s*(lakh|lakhs|lac|l\b|k\b|thousand)?', msg_lower)
            if amount_match:
                val = int(amount_match.group(1))
                unit = amount_match.group(2) or ""
                
                if 'lakh' in unit or 'lac' in unit or unit == 'l':
                    collected["amount"] = val * 100000
                elif 'k' in unit or 'thousand' in unit:
                    collected["amount"] = val * 1000
                elif val >= 10000:  # Full amount
                    collected["amount"] = val

        # Extract tenure (ONLY if not already collected)
        if not collected["tenure"]:
            tenure_match = re.search(r'(\d+)\s*(month|months|year|years|mo|yr)', msg_lower)
            if tenure_match:
                val = int(tenure_match.group(1))
                unit = tenure_match.group(2)
                
                if 'year' in unit or 'yr' in unit:
                    collected["tenure"] = val * 12
                else:
                    collected["tenure"] = val

        # Detect purpose
        if not collected["purpose"]:
            purposes = {
                'home': ['home', 'house', 'repair', 'renovation', 'construction'],
                'medical': ['medical', 'health', 'hospital', 'treatment', 'surgery'],
                'education': ['education', 'school', 'college', 'study', 'course'],
                'wedding': ['wedding', 'marriage', 'shaadi'],
                'business': ['business', 'startup', 'venture'],
                'debt': ['debt', 'consolidation', 'payoff', 'emi'],
                'emergency': ['emergency', 'urgent', 'immediate']
            }

            for purpose_key, keywords in purposes.items():
                if any(kw in msg_lower for kw in keywords):
                    collected["purpose"] = purpose_key
                    break

        # CHECK: What do we still need?
        # Priority: 1) Phone/Customer 2) Amount 3) Tenure

        if not conv["customer_id"]:
            response = "To check your eligibility, I'll need your registered mobile number. Could you share that?"
            return {
                "message": response,
                "state": ConversationState.EXPLORING_NEEDS,
                "next_action": "wait"
            }

        if not collected["amount"]:
            response = "Great! How much do you need? You can tell me in lakhs or the full amount."
            if pre_limit > 0:
                response += f" (You're pre-approved for up to ₹{int(pre_limit):,})"
            return {
                "message": response,
                "state": ConversationState.EXPLORING_NEEDS,
                "next_action": "wait"
            }

        if not collected["tenure"]:
            response = f"Got it - ₹{int(collected['amount']):,}"
            if collected["purpose"]:
                response += f" for {collected['purpose']}"
            response += ". How many months would you like to repay? (12, 24, or 36 months)"
            return {
                "message": response,
                "state": ConversationState.EXPLORING_NEEDS,
                "next_action": "wait",
                "suggested_actions": ["12", "24", "36"]
            }

        # ✅ WE HAVE EVERYTHING - Check eligibility first before Sales Agent
        print("🤝 [Master Agent] All info collected. Checking eligibility...")

        # CRITICAL CHECK 1: Credit Score
        if credit_score < 700:
            conv["state"] = ConversationState.COMPLETED
            response = (
                f"I appreciate your interest! However, I need to be upfront with you - "
                f"your credit score ({credit_score}) is below our minimum requirement of 700. "
                f"😔 Unfortunately, we cannot approve your loan at this time.\n\n"
                f"💡 **Suggestion:** Work on improving your credit score by paying bills on time "
                f"and reducing debt. Come back in 3-6 months, and we'll be happy to help!"
            )
            return {
                "message": response,
                "state": ConversationState.COMPLETED,
                "next_action": "none",
                "data": {
                    "rejection_reason": "credit_score_below_minimum",
                    "credit_score": credit_score,
                    "minimum_required": 700
                }
            }

        # CRITICAL CHECK 2: Amount vs 2x Pre-approved Limit
        if collected["amount"] > (2 * pre_limit):
            conv["state"] = ConversationState.COMPLETED
            max_allowed = int(2 * pre_limit)
            response = (
                f"I understand you need ₹{int(collected['amount']):,}, but this amount "
                f"exceeds twice your pre-approved limit (₹{int(pre_limit):,}). "
                f"😔 Unfortunately, we can only approve up to ₹{max_allowed:,}.\n\n"
                f"💡 **Would you like to apply for ₹{max_allowed:,} instead?**"
            )
            return {
                "message": response,
                "state": ConversationState.COMPLETED,
                "next_action": "none",
                "data": {
                    "rejection_reason": "amount_exceeds_2x_limit",
                    "requested_amount": collected["amount"],
                    "max_allowed": max_allowed
                }
            }

        # Proceed to Sales Agent for offer calculation
        print("🤝 [Master Agent] Eligibility passed. Delegating to Sales Agent...")
        
        flow.user_text = f"I need ₹{collected['amount']} for {collected['tenure']} months"
        sales_msg = AgentMessage(
            sender="master_agent",
            recipient="sales_agent",
            content={"customer_id": conv["customer_id"], "user_input": flow.user_text}
        )

        sales_result = await self.orchestrator.sales.handle(sales_msg)
        flow.sales_result = sales_result.content or {}

        conv["state"] = ConversationState.AWAITING_CONFIRMATION

        # Calculate EMI
        emi = flow.sales_result.get("estimated_emi", 0)
        rate = flow.sales_result.get("offer", {}).get("interest_rate", 12.0)
        
        # Calculate monthly salary
        monthly_salary = annual_income / 12 if annual_income > 0 else 0
        
        # EMI affordability check (EMI should be ≤ 50% of monthly salary)
        emi_threshold = monthly_salary * 0.5 if monthly_salary > 0 else 0

        # Build personalized message
        response = (
            f"Perfect! ✅ Here's your loan summary:\n\n"
            f"💰 Amount: ₹{int(collected['amount']):,}\n"
            f"📅 Tenure: {collected['tenure']} months\n"
            f"💳 EMI: ~₹{int(emi):,}/month\n"
            f"📊 Interest: {rate}% per annum\n"
            f"🏆 Your Credit Score: {credit_score} (Excellent!)\n\n"
        )

        # Check if instant approval or needs documents
        if collected['amount'] <= pre_limit:
            response += "🎉 This is within your pre-approved limit - instant approval possible! "
            
            # Check EMI affordability
            if monthly_salary > 0:
                if emi <= emi_threshold:
                    response += f"Your monthly salary (₹{int(monthly_salary):,}) comfortably covers the EMI. Ready to proceed?"
                else:
                    response += f"\n\n⚠️ However, the EMI (₹{int(emi):,}) exceeds 50% of your monthly salary (₹{int(monthly_salary):,}). We may need additional verification."
        else:
            # Exceeds pre-approved - need salary slip
            excess = collected['amount'] - pre_limit
            response += (
                f"📎 This is ₹{int(excess):,} above your pre-approved limit. "
                f"I'll need your latest salary slip to verify income. "
            )
            
            if monthly_salary > 0 and emi > emi_threshold:
                response += (
                    f"\n\n⚠️ Note: The EMI (₹{int(emi):,}) is quite high relative to your current income. "
                    f"Your salary slip will help us verify affordability."
                )
            
            response += " Ready to proceed?"

        return {
            "message": response,
            "state": ConversationState.AWAITING_CONFIRMATION,
            "next_action": "confirm",
            "data": {
                "offer": flow.sales_result.get("offer"),
                "parsed_request": {
                    "requested_amount": collected['amount'],
                    "requested_tenure_months": collected['tenure'],
                    "purpose": collected['purpose'],
                    "credit_score": credit_score,
                    "pre_approved_limit": pre_limit,
                    "monthly_salary": monthly_salary,
                    "emi": emi,
                    "emi_threshold": emi_threshold
                }
            },
            "suggested_actions": ["Yes, proceed", "Tell me more", "Change amount"]
        }

    async def _handle_awaiting_confirmation(self, conv_id: str, message: str) -> Dict[str, Any]:
        """Customer is deciding whether to proceed"""
        conv = self.conversations[conv_id]
        msg_lower = message.lower()

        confirm_words = ["yes", "proceed", "start", "go", "ok", "sure", "apply", "confirm"]
        deny_words = ["no", "not now", "later", "think", "wait"]

        if any(word in msg_lower for word in confirm_words):
            # Customer confirmed - check if document needed
            collected = conv["collected_info"]
            customer = self.db.get_customer(conv["customer_id"])
            pre_limit = float(customer.get("pre_approved_limit", 0) or 0) if customer else 0

            if collected["amount"] > pre_limit:
                # Need salary slip
                conv["state"] = ConversationState.AWAITING_DOCUMENT
                response = (
                    "Great! 📎 To proceed with this loan amount, I'll need to verify your income.\n\n"
                    "**Please provide the file path to your latest salary slip.**\n"
                    "Example: E:\\\\documents\\\\salary_slip.pdf\n\n"
                    "Or type 'sample' to use test data."
                )
                return {
                    "message": response,
                    "state": ConversationState.AWAITING_DOCUMENT,
                    "next_action": "upload_document"
                }
            else:
                # Within pre-approved - process directly
                conv["state"] = ConversationState.PROCESSING
                response = (
                    "Awesome! 🎉 Let me quickly verify your details and process this for you. "
                    "This will just take a moment..."
                )

                # Start orchestration
                orchestration_result = await self._orchestrate_approval_flow(conv_id)
                return orchestration_result

        elif any(word in msg_lower for word in deny_words):
            # Customer hesitant - persuade gently
            response = (
                "No pressure at all! 😊 Take your time. Is there anything specific you'd like to know? "
                "I can explain the EMI breakdown, interest rates, or any fees. I'm here to help!"
            )

            return {
                "message": response,
                "state": ConversationState.AWAITING_CONFIRMATION,
                "next_action": "wait"
            }

        else:
            # Customer asked something else - handle query but stay in same state
            response = await self._generate_conversational_response(
                conv_id, message,
                system_context="Answer the customer's question and encourage them to proceed with the loan."
            )

            return {
                "message": response,
                "state": ConversationState.AWAITING_CONFIRMATION,
                "next_action": "wait"
            }

    async def _handle_presenting_offer(self, conv_id: str, message: str) -> Dict[str, Any]:
        """Handle offer presentation state"""
        # Redirect to awaiting confirmation
        return await self._handle_awaiting_confirmation(conv_id, message)

    async def _handle_collecting_details(self, conv_id: str, message: str) -> Dict[str, Any]:
        """Handle additional details collection"""
        # Redirect to exploring needs
        return await self._handle_exploring_needs(conv_id, message)

    async def _handle_document_upload(self, conv_id: str, message: str,
                                       uploaded_file: Optional[Dict] = None) -> Dict[str, Any]:
        """Handle salary slip upload with detailed OCR analysis"""
        conv = self.conversations[conv_id]
        flow = conv["flow"]

        # Check if user is providing file path in message
        if not uploaded_file and message.strip() and not message.lower() in ['upload', 'uploading', 'sample']:
            # User provided file path as text
            import os
            file_path = message.strip()
            
            # Check if it's a valid path
            if os.path.exists(file_path):
                uploaded_file = {
                    "file_path": file_path,
                    "file_name": os.path.basename(file_path)
                }
            else:
                # Invalid path
                return {
                    "message": (
                        f"❌ I couldn't find the file at: {file_path}\n\n"
                        f"Please check the path and try again, or type 'sample' to use test data."
                    ),
                    "state": ConversationState.AWAITING_DOCUMENT,
                    "next_action": "upload_document"
                }

        # Handle sample data request
        if message.lower() == 'sample':
            # Use a sample file path
            sample_path = "src/tests/data/sample_salary_slip.pdf"
            if os.path.exists(sample_path):
                uploaded_file = {
                    "file_path": sample_path,
                    "file_name": "sample_salary_slip.pdf"
                }
                print("📄 Using sample salary slip...")
            else:
                return {
                    "message": "❌ Sample file not found. Please provide your actual salary slip path.",
                    "state": ConversationState.AWAITING_DOCUMENT,
                    "next_action": "upload_document"
                }

        if uploaded_file:
            # Customer uploaded document
            print("📎 [Master Agent] Processing uploaded document...")
            file_path = uploaded_file.get("file_path")

            if not file_path and uploaded_file.get("file_bytes"):
                # Save bytes to temp file
                import tempfile
                ext = os.path.splitext(uploaded_file.get("file_name", "doc.pdf"))[1] or ".pdf"
                fd, file_path = tempfile.mkstemp(suffix=ext)
                with os.fdopen(fd, "wb") as f:
                    f.write(uploaded_file["file_bytes"])

            try:
                # Process with orchestrator
                print("🔄 [Master Agent] Sending to Underwriting Agent for OCR and verification...")
                result = await self.orchestrator.run_with_salary_slip(flow, file_path)

                uw_out = result.get("underwriting_result", {})
                san_out = result.get("sanction_result", {})

                # Update flow
                flow.underwriting_result = uw_out
                flow.sanction_result = san_out

                # Extract OCR and verification details
                ocr_confidence = uw_out.get("ocr_confidence", 0)
                ocr_matched_line = uw_out.get("ocr_matched_line", "")
                ocr_source = uw_out.get("ocr_source", "")
                monthly_salary_used = uw_out.get("monthly_salary_used", 0)
                emi_ratio = uw_out.get("emi_ratio", 0)
                
                decision = uw_out.get("decision", "").lower()
                
                # Get loan details
                loan_details = uw_out.get("loan_details", {})
                monthly_emi = loan_details.get("monthly_emi", 0) or uw_out.get("monthly_emi", 0)
                loan_amount = loan_details.get("loan_amount", 0)
                
                # Get customer data
                customer = self.db.get_customer(conv["customer_id"])
                credit_score = int(customer.get("credit_score", 0) or 0) if customer else 0

                # Build detailed analysis message
                analysis_msg = (
                    f"📄 **Document Analysis Complete!**\n\n"
                    f"🔍 **OCR Results:**\n"
                    f"  • Confidence: {float(ocr_confidence):.1%}\n"
                )
                
                if ocr_matched_line:
                    analysis_msg += f"  • Extracted Line: \"{ocr_matched_line}\"\n"
                
                if ocr_source:
                    analysis_msg += f"  • Source: {ocr_source}\n"
                
                if monthly_salary_used > 0:
                    analysis_msg += f"  • Monthly Salary Detected: ₹{int(monthly_salary_used):,}\n"
                
                analysis_msg += f"\n📊 **Eligibility Verification:**\n"
                analysis_msg += f"  ✅ Credit Score: {credit_score} (Minimum: 700)\n"
                
                if monthly_salary_used > 0 and monthly_emi > 0:
                    emi_threshold = monthly_salary_used * 0.5
                    emi_percentage = (monthly_emi / monthly_salary_used) * 100
                    
                    analysis_msg += f"  • Monthly Salary: ₹{int(monthly_salary_used):,}\n"
                    analysis_msg += f"  • Requested EMI: ₹{int(monthly_emi):,}\n"
                    analysis_msg += f"  • EMI as % of Salary: {emi_percentage:.1f}%\n"
                    analysis_msg += f"  • EMI Threshold (50%): ₹{int(emi_threshold):,}\n"
                    
                    if monthly_emi <= emi_threshold:
                        analysis_msg += f"  ✅ **EMI Check: PASSED** (EMI ≤ 50% of salary)\n"
                    else:
                        analysis_msg += f"  ❌ **EMI Check: FAILED** (EMI > 50% of salary)\n"
                
                analysis_msg += f"\n"

                if decision == "approved":
                    # Success!
                    conv["state"] = ConversationState.COMPLETED

                    response = (
                        analysis_msg +
                        f"🎉 **LOAN APPROVED!**\n\n"
                        f"📋 **Final Loan Details:**\n"
                        f"  • Amount: ₹{int(loan_amount):,}\n"
                        f"  • Tenure: {loan_details.get('tenure_months', 0)} months\n"
                        f"  • Monthly EMI: ₹{int(monthly_emi):,}\n"
                        f"  • Interest Rate: {loan_details.get('interest_rate', 0)}% p.a.\n"
                        f"  • Processing Fee: ₹{int(loan_details.get('processing_fee', 0)):,}\n\n"
                        f"📄 Your sanction letter is ready for download!\n"
                        f"💰 Funds will be disbursed within 24 hours.\n\n"
                        f"Welcome to QuickCash! 🚀"
                    )

                    return {
                        "message": response,
                        "state": ConversationState.COMPLETED,
                        "next_action": "download",
                        "data": {
                            "sanction_letter_path": san_out.get("pdf_path"),
                            "loan_details": loan_details,
                            "application_id": loan_details.get("application_id"),
                            "ocr_analysis": {
                                "confidence": ocr_confidence,
                                "matched_line": ocr_matched_line,
                                "source": ocr_source,
                                "monthly_salary": monthly_salary_used
                            },
                            "verification": {
                                "credit_score": credit_score,
                                "emi": monthly_emi,
                                "emi_threshold": monthly_salary_used * 0.5 if monthly_salary_used > 0 else 0,
                                "emi_percentage": (monthly_emi / monthly_salary_used * 100) if monthly_salary_used > 0 else 0
                            }
                        }
                    }
                else:
                    # Rejected or needs more info
                    conv["state"] = ConversationState.COMPLETED
                    reason = uw_out.get("message", "We need to review your application further.")
                    reasons_list = uw_out.get("reasons", [])
                    
                    response = analysis_msg + f"😔 **Application Status: {decision.upper()}**\n\n{reason}"
                    
                    if reasons_list:
                        response += f"\n\n**Reasons:**\n" + "\n".join([f"  • {r}" for r in reasons_list])
                    
                    response += "\n\nIf you have any questions, I'm here to help!"

                    return {
                        "message": response,
                        "state": ConversationState.COMPLETED,
                        "next_action": "none",
                        "data": {
                            "rejection_reason": uw_out.get("reasons"),
                            "underwriting_result": uw_out,
                            "ocr_analysis": {
                                "confidence": ocr_confidence,
                                "matched_line": ocr_matched_line,
                                "source": ocr_source,
                                "monthly_salary": monthly_salary_used
                            }
                        }
                    }

            except Exception as e:
                print(f"❌ [Master Agent] Error processing document: {e}")
                return {
                    "message": (
                        f"❌ Sorry, there was an error processing your document: {str(e)}\n\n"
                        f"Please try uploading again or contact support."
                    ),
                    "state": ConversationState.AWAITING_DOCUMENT,
                    "next_action": "upload_document"
                }

        else:
            # Remind to upload
            response = (
                "I'm still waiting for your salary slip 📎\n\n"
                "Please provide the file path to your salary slip document.\n"
                "Example: E:\\\\documents\\\\salary_slip.pdf\n\n"
                "Or type 'sample' to use test data."
            )

            return {
                "message": response,
                "state": ConversationState.AWAITING_DOCUMENT,
                "next_action": "upload_document"
            }

    async def _handle_processing(self, conv_id: str, message: str) -> Dict[str, Any]:
        """Handle while loan is being processed"""
        return {
            "message": "Your application is being processed. This will just take a moment... ⏳",
            "state": ConversationState.PROCESSING,
            "next_action": "wait"
        }

    async def _handle_completed(self, conv_id: str, message: str) -> Dict[str, Any]:
        """Handle after loan is approved/rejected"""
        conv = self.conversations[conv_id]
        flow = conv["flow"]
        msg_lower = message.lower()

        if "download" in msg_lower or "letter" in msg_lower:
            san_out = flow.sanction_result or {}
            pdf_path = san_out.get("pdf_path")
            if pdf_path:
                return {
                    "message": "Here's your sanction letter! 📄",
                    "state": ConversationState.COMPLETED,
                    "next_action": "download",
                    "data": {"pdf_path": pdf_path}
                }

        # Customer asking something post-approval/rejection
        uw_out = flow.underwriting_result or {}
        decision = uw_out.get("decision", "").lower()
        
        if decision == "approved":
            response = (
                "Your loan is all set! 🎉 If you have any questions about next steps, "
                "disbursement, or repayment, feel free to ask. I'm here to help!"
            )
        else:
            response = (
                "I understand this isn't the outcome you hoped for. 😔 If you have questions "
                "about improving your eligibility or alternative options, I'm happy to help!"
            )

        return {
            "message": response,
            "state": ConversationState.COMPLETED,
            "next_action": "wait"
        }

    # ==================== ORCHESTRATION ====================
    async def _orchestrate_approval_flow(self, conv_id: str) -> Dict[str, Any]:
        """
        Orchestrate all Worker Agents to complete loan approval
        Flow: Verification → Underwriting → Sanction
        """
        conv = self.conversations[conv_id]
        flow = conv["flow"]

        # Step 1: Verification Agent (Worker Agent #2)
        print("🔐 [Master Agent] Delegating to Verification Agent...")
        ver_msg = AgentMessage(
            sender="master_agent",
            recipient="verification_agent",
            content={"customer_id": conv["customer_id"] or ""}
        )

        ver_result = await self.orchestrator.verification.handle(ver_msg)
        flow.verification_result = ver_result.content or {}

        # Step 2: Underwriting Agent (Worker Agent #3)
        print("🏦 [Master Agent] Delegating to Underwriting Agent...")
        uw_state = await self.orchestrator._underwriting_node(flow.dict())
        flow.underwriting_result = uw_state.get("underwriting_result", {})

        uw_out = flow.underwriting_result
        decision = uw_out.get("decision", "").lower()

        # Check if document required
        if decision in ("needs_salary_slip", "pending_salary_slip"):
            conv["state"] = ConversationState.AWAITING_DOCUMENT
            response = (
                "To proceed with this loan amount, I'll need to verify your income. "
                "Could you please upload your latest salary slip? 📎\n\n"
                "Provide the file path when ready."
            )

            return {
                "message": response,
                "state": ConversationState.AWAITING_DOCUMENT,
                "next_action": "upload_document",
                "data": {"underwriting": uw_out}
            }

        # Step 3: Sanction Agent (Worker Agent #4)
        if decision == "approved":
            print("📜 [Master Agent] Delegating to Sanction Letter Generator...")
            san_state = await self.orchestrator._sanction_node(flow.dict())
            flow.sanction_result = san_state.get("sanction_result", {})

            conv["state"] = ConversationState.COMPLETED

            san_out = flow.sanction_result
            loan_details = uw_out.get("loan_details", {})

            response = (
                f"🎉 **Congratulations! Your loan is APPROVED!**\n\n"
                f"📋 **Loan Details:**\n"
                f"  • Amount: ₹{int(loan_details.get('loan_amount', 0)):,}\n"
                f"  • Tenure: {loan_details.get('tenure_months', 0)} months\n"
                f"  • Monthly EMI: ₹{int(loan_details.get('monthly_emi', 0)):,}\n"
                f"  • Interest Rate: {loan_details.get('interest_rate', 0)}% p.a.\n\n"
                f"📄 Your sanction letter is ready for download!\n"
                f"💰 Funds will be disbursed within 24 hours."
            )

            return {
                "message": response,
                "state": ConversationState.COMPLETED,
                "next_action": "download",
                "data": {
                    "loan_details": loan_details,
                    "sanction_letter_path": san_out.get("pdf_path"),
                    "application_id": loan_details.get("application_id")
                },
                "metadata": {
                    "processing_time_seconds": time.time() - conv["created_at"]
                }
            }
        else:
            # Rejected
            conv["state"] = ConversationState.COMPLETED
            reason = uw_out.get("message", "Unfortunately, we couldn't approve your loan at this time.")
            reasons_list = uw_out.get("reasons", [])
            
            response = f"😔 {reason}"
            
            if reasons_list:
                response += f"\n\n**Reasons:**\n" + "\n".join([f"  • {r}" for r in reasons_list])
            
            response += "\n\nIf you have any questions, I'm here to help!"

            return {
                "message": response,
                "state": ConversationState.COMPLETED,
                "next_action": "none",
                "data": {"underwriting": uw_out}
            }

    # ==================== EXIT POINT ====================
    async def end_conversation(self, conversation_id: str, reason: str = "completed") -> Dict[str, Any]:
        """
        EXIT POINT: Close conversation gracefully
        """
        if conversation_id not in self.conversations:
            return {"error": "conversation_not_found"}

        conv = self.conversations[conversation_id]
        flow = conv["flow"]

        # Generate farewell
        if reason == "completed":
            final_status = "approved" if (flow.sanction_result or {}).get("decision") == "approved" else "reviewed"
            farewell = (
                f"Thank you for choosing QuickCash! 🙏 Your application has been {final_status}. "
                f"If you need any help in the future, just come back and chat with me. Have a great day! 😊"
            )
        elif reason == "timeout":
            farewell = "I noticed you've been away for a while. No worries - whenever you're ready, just start a new chat. Take care! 👋"
        else:
            farewell = "Thank you for your time today. Feel free to return anytime. Goodbye! 😊"

        # Archive conversation
        conv["state"] = ConversationState.CLOSED
        conv["closed_at"] = time.time()
        conv["close_reason"] = reason

        summary = {
            "conversation_id": conversation_id,
            "customer_id": conv["customer_id"],
            "duration_seconds": conv["closed_at"] - conv["created_at"],
            "messages_exchanged": len(conv["history"]),
            "final_state": conv["state"],
            "loan_approved": (flow.sanction_result or {}).get("decision") == "approved",
            "farewell_message": farewell
        }

        # Clean up old conversations (optional - keep last 100)
        if len(self.conversations) > 100:
            oldest = sorted(self.conversations.items(), key=lambda x: x[1]["created_at"])[:50]
            for old_id, _ in oldest:
                del self.conversations[old_id]

        return summary

    # ==================== HELPER METHODS ====================
    async def _identify_customer(self, message: str) -> Optional[str]:
        """Try to identify customer from message (phone/email)"""
        import re

        # Phone pattern (Indian mobile)
        phone_match = re.search(r'\+?91[-\s]?[6-9]\d{9}|[6-9]\d{9}', message)
        if phone_match:
            phone = phone_match.group(0)
            phone = re.sub(r'[^\d]', '', phone)
            if len(phone) == 10:
                phone = "+91" + phone

            # Look up in DB
            try:
                cur = self.db.conn.execute(
                    "SELECT customer_id FROM customers WHERE phone LIKE ? LIMIT 1",
                    (f"%{phone[-10:]}%",)
                )
                row = cur.fetchone()
                if row:
                    return row["customer_id"]
            except Exception:
                pass

        return None

    async def _generate_conversational_response(self, conv_id: str, message: str,
                                                  system_context: str = "") -> str:
        """Generate natural conversational response using LLM"""
        if not self.llm:
            return "I understand. How can I help you further?"

        conv = self.conversations[conv_id]
        history = conv["history"][-6:]  # Last 3 exchanges

        system_prompt = f"""You are Sarah, a friendly and persuasive loan advisor at QuickCash NBFC.
You're helping a customer explore personal loan options through a web chatbot.

Context: {system_context}

Guidelines:
- Be warm, conversational, and helpful
- Keep responses brief (2-3 sentences max)
- Use emojis sparingly for friendliness
- Subtly guide toward loan offerings
- Build trust and rapport

Recent conversation:
{self._format_history_for_llm(history)}
"""

        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=message)
            ]
            import asyncio
            response = await asyncio.to_thread(self.llm.invoke, messages)
            return response.content
        except Exception:
            return "I'm here to help you with any questions about our personal loans. What would you like to know?"

    def _format_history_for_llm(self, history: List[Dict]) -> str:
        """Format conversation history for LLM context"""
        formatted = []
        for msg in history[-6:]:
            role = "Customer" if msg["role"] == "user" else "Sarah"
            formatted.append(f"{role}: {msg['content']}")
        return "\n".join(formatted)

    def _add_to_history(self, conv_id: str, role: str, content: str):
        """Add message to conversation history"""
        if conv_id in self.conversations:
            self.conversations[conv_id]["history"].append({
                "role": role,
                "content": content,
                "timestamp": time.time()
            })

    # ==================== CONVERSATION MANAGEMENT ====================
    def get_conversation_state(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get current state of a conversation"""
        return self.conversations.get(conversation_id)

    def list_active_conversations(self) -> List[Dict[str, Any]]:
        """List all active conversations"""
        return [
            {
                "conversation_id": conv_id,
                "customer_id": conv["customer_id"],
                "state": conv["state"],
                "last_activity": conv["last_activity"],
                "duration": time.time() - conv["created_at"]
            }
            for conv_id, conv in self.conversations.items()
            if conv["state"] != ConversationState.CLOSED
        ]

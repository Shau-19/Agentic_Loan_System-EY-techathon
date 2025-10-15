"""
    SalesAgent: conversation-first sales advisor.

    Returns structured content:
      - message: human-friendly phrase
      - offer: optional dict with offer info
      - parsed_request: parsed requested_amount, requested_tenure_months
      - estimated_emi: int or None
      - requires_confirmation: True when parsed_request contains amount+tenure
      - ready_to_process: True when UI can show a loan summary and expect confirmation
      - auto_start: True if the user explicitly included a confirmation token in the same message
    """

# src/agents/sales_agent.py
import asyncio
from typing import Dict, Any
from dotenv import load_dotenv
import os

# --- load .env from project src/ directory ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../src
dotenv_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=dotenv_path)

# Try to import an LLM wrapper; if missing, we will fallback to a deterministic reply.
_CHAT_AVAILABLE = False
try:
    from langchain_groq import ChatGroq
    from langchain.schema import SystemMessage, HumanMessage
    _CHAT_AVAILABLE = True
except Exception:
    _CHAT_AVAILABLE = False

from src.agents.base_agent import BaseAgent, AgentMessage
from src.data.database import NBFCDatabase


class SalesAgent(BaseAgent):
   

    def __init__(self, model_name: str = "llama-3.3-70b-versatile"):
        super().__init__("sales_agent", "sales")
        self.db = NBFCDatabase()

        # LLM: optional but preferred for natural clarifying questions
        if _CHAT_AVAILABLE:
            try:
                self.llm = ChatGroq(model=model_name, temperature=0.2)
            except Exception:
                # If instantiation fails, mark not available to fall back
                self.llm = None
        else:
            self.llm = None

    async def handle(self, message: AgentMessage) -> AgentMessage:
        content = message.content or {}
        customer_id = content.get("customer_id") or ""
        user_text = (content.get("user_input") or "") or ""

        # Fetch customer & pre-approved offer
        customer = self.db.get_customer(customer_id) if customer_id else None
        offer = None
        if customer:
            # conservative defaults if DB is missing fields
            offer_amount = int(customer.get("pre_approved_limit") or customer.get("pre_approved_amount") or 500000)
            offer_rate = 12.0
            offer = {"max_amount": offer_amount, "interest_rate": offer_rate, "tenure_months": [12, 24, 36]}

        # --- parsing helpers ---
        import re

        def parse_amount(text: str):
            if not text:
                return None
            t = text.lower().replace(",", "").strip()
            # lakh / lac / crore handling
            m = re.search(r'([\d\.]+)\s*(lakh|lakhs|lac|lacs)', t)
            if m:
                try:
                    return int(float(m.group(1)) * 100000)
                except Exception:
                    pass
            m = re.search(r'([\d\.]+)\s*(crore|crores)', t)
            if m:
                try:
                    return int(float(m.group(1)) * 10000000)
                except Exception:
                    pass
            # look for contiguous digits of 4+ length (e.g., 250000)
            m = re.search(r'(\d{4,})', t)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    pass
            # also check for patterns like "25k" or "25,000"
            m = re.search(r'([\d\.]+)\s*k\b', t)
            if m:
                try:
                    return int(float(m.group(1)) * 1000)
                except Exception:
                    pass
            return None

        def parse_tenure_months(text: str):
            if not text:
                return None
            m = re.search(r'(\d+)\s*years?', text.lower())
            if m:
                try:
                    return int(m.group(1)) * 12
                except Exception:
                    pass
            m = re.search(r'(\d+)\s*months?', text.lower())
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    pass
            # also allow "6 mo" style
            m = re.search(r'(\d+)\s*mo', text.lower())
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    pass
            return None

        requested_amount = parse_amount(user_text)
        requested_tenure = parse_tenure_months(user_text)

        # EMI helper
        def compute_emi(principal, annual_rate, months):
            try:
                if not principal or not months:
                    return None
                r = annual_rate / 12.0 / 100.0
                if r == 0:
                    return int(principal / months)
                emi = (principal * r * (1 + r) ** months) / ((1 + r) ** months - 1)
                return int(round(emi))
            except Exception:
                return None

        est_interest_rate = offer["interest_rate"] if offer else 12.0
        estimated_emi = compute_emi(requested_amount, est_interest_rate, requested_tenure) if (
            requested_amount and requested_tenure
        ) else None

        # detect explicit confirmation tokens in user text (if user already said 'start' etc.)
        confirm_tokens = [
            "start", "proceed", "start application", "yes start", "apply", "process",
            "submit", "go ahead", "confirm", "lets do it", "let's do it", "sounds good",
            "i'm in", "i am in", "ok do it", "do it", "start now"
        ]
        user_lower = (user_text or "").lower()
        has_confirmation = any(tok in user_lower for tok in confirm_tokens)

        # ---------- MAIN LOGIC ----------
        # If both amount and tenure are present, deterministic behavior but DON'T auto-run processing by default.
        if requested_amount and requested_tenure:
            msg = (
                f"Great! You’re eligible for a loan of ₹{requested_amount:,} "
                f"for {requested_tenure} months at {est_interest_rate}% annual interest."
            )
            if estimated_emi:
                msg += f" Your estimated EMI will be around ₹{estimated_emi:,} per month."

            # explicit CTA — require confirmation before heavy processing
            if has_confirmation:
                msg += " I see you asked to start — I will proceed with the application now."
                ready_flag = True
                auto_flag = True
            else:
                msg += " If you'd like to proceed, reply with **Start application**, **Proceed**, or **Yes, start**. You can also change amount or tenure."
                ready_flag = True
                auto_flag = False

            reply_content = {
                "message": msg,
                "offer": offer,
                "parsed_request": {
                    "requested_amount": requested_amount,
                    "requested_tenure_months": requested_tenure,
                },
                "estimated_emi": estimated_emi,
                # NEW flags for orchestrator/master to inspect
                "requires_confirmation": True,
                "ready_to_process": ready_flag,
                "auto_start": auto_flag,
            }
        else:
            # Not enough structured info — ask clarifying question
            # Prefer LLM when available to keep the conversation natural; otherwise fall back to deterministic prompts
            if self.llm:
                sys_prompt = """
You are Sarah — a persuasive, friendly, and emotionally intelligent loan advisor for QuickCash NBFC.
Keep replies short and human (1–3 sentences). Ask clarifying questions to get loan amount, tenure (months/years) and purpose.
If the customer already gave amount and tenure, acknowledge and suggest how to proceed.
Always use the customer's language (English/Hindi).
"""
                if offer:
                    human_prompt = (
                        f"Customer says: {user_text}\n"
                        f"Customer name: {customer.get('name')}.\n"
                        f"Pre-approved limit: ₹{offer['max_amount']:,} at {offer['interest_rate']}% interest.\n"
                        "Respond appropriately — present offer if relevant, and ask the next question toward application."
                    )
                else:
                    human_prompt = f"Customer says: {user_text}\nAsk about loan amount, tenure, and purpose naturally."

                def llm_call():
                    return self.llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=human_prompt)])

                try:
                    resp = await asyncio.to_thread(llm_call)
                    msg = resp.content
                except Exception:
                    # fallback deterministic message
                    if requested_amount and not requested_tenure:
                        msg = "Thanks — what's the tenure (in months or years) you'd prefer?"
                    elif requested_tenure and not requested_amount:
                        msg = "Thanks — how much would you like to borrow?"
                    else:
                        msg = "Hi — could you tell me the loan amount you want and how long you'd like to repay (months or years)?"
            else:
                # deterministic fallback prompt (no LLM)
                if requested_amount and not requested_tenure:
                    msg = "Thanks — what's the tenure (in months or years) you'd prefer?"
                elif requested_tenure and not requested_amount:
                    msg = "Thanks — how much would you like to borrow?"
                else:
                    msg = "Hi — could you tell me the loan amount you want and how long you'd like to repay (months or years)?"

            reply_content = {
                "message": msg,
                "offer": offer,
                "parsed_request": {
                    "requested_amount": requested_amount,
                    "requested_tenure_months": requested_tenure,
                },
                "estimated_emi": estimated_emi,
                "requires_confirmation": False,
                "ready_to_process": False,
                "auto_start": False,
            }

        return AgentMessage(sender=self.agent_id, recipient=message.sender, content=reply_content)

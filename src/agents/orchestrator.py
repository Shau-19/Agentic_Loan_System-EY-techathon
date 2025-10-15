
# src/agents/orchestrator.py
import asyncio
import logging
import re
import os
import time
import json
from typing import Dict, Any, Optional, TypedDict, Annotated
from operator import add

from pydantic import BaseModel, Field

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from src.agents.base_agent import AgentMessage
from src.agents.sales_agent import SalesAgent
from src.agents.verification_agent import VerificationAgent
from src.agents.underwriting_agent import UnderwritingAgent
from src.agents.sanction_agent import SanctionAgent
from src.data.database import NBFCDatabase

LOG = logging.getLogger("orchestrator")
LOG.setLevel(logging.DEBUG)
if not LOG.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    LOG.addHandler(ch)


class LoanFlowState(BaseModel):
    """Shared state model for the flow."""
    user_text: str = ""
    customer_id: str = ""
    sales_result: Dict[str, Any] = Field(default_factory=dict)
    verification_result: Dict[str, Any] = Field(default_factory=dict)
    underwriting_result: Dict[str, Any] = Field(default_factory=dict)
    sanction_result: Dict[str, Any] = Field(default_factory=dict)
    auto_approve: bool = False
    salary_slip_path: str = ""


class LoanOrchestrator:
    """LangGraph-based orchestrator for NBFC loan workflow."""

    def __init__(self):
        self.sales = SalesAgent()
        self.verification = VerificationAgent()
        self.underwriting = UnderwritingAgent()
        self.sanction = SanctionAgent()
        self.db = NBFCDatabase()
        
        # Manual review directory
        self.manual_review_dir = os.path.join(os.getcwd(), "manual_reviews")
        os.makedirs(self.manual_review_dir, exist_ok=True)
        
        # Build the LangGraph workflow
        self.workflow = self._build_workflow()
        self.memory = MemorySaver()
        self.app = self.workflow.compile(checkpointer=self.memory)

    def _coerce_amount(self, val: Any) -> Optional[float]:
        """Attempt to convert a variety of amount formats into a float."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            s = val.strip()
            s = re.sub(r"[^\d.\-]", "", s)
            if not s:
                return None
            digits_only = re.sub(r"\D", "", s)
            try:
                if len(digits_only) >= 10 and int(digits_only) > 1_000_000_00:
                    LOG.debug("[ORCHESTRATOR] _coerce_amount: value looks like phone/garbage -> %s", val)
                    return None
            except Exception:
                pass
            try:
                return float(s)
            except Exception:
                return None
        return None

    # -------------------------
    #  Node implementations
    # -------------------------
    async def _sales_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Call SalesAgent and return its result merged to state."""
        # Handle both dict and LoanFlowState inputs
        if isinstance(state, LoanFlowState):
            state = state.dict()
        msg = AgentMessage(
            sender="orchestrator",
            recipient="sales_agent",
            content={
                "customer_id": state.get("customer_id", ""),
                "user_input": state.get("user_text", "")
            }
        )
        result = await self.sales.handle(msg)
        sales_out = result.content or {}
        if not isinstance(sales_out, dict):
            LOG.warning("SalesAgent returned non-dict content; coercing to dict")
            sales_out = {"raw": sales_out}
        LOG.info("[ORCHESTRATOR][SALES] customer=%s sales_out_keys=%s", state.get("customer_id"), list(sales_out.keys()))
        print(f"[SALES] customer={state.get('customer_id')} sales_out={sales_out}")
        return {"sales_result": sales_out}

    async def _verification_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Call VerificationAgent for KYC / identity only."""
        # Handle both dict and LoanFlowState inputs
        if isinstance(state, LoanFlowState):
            state = state.dict()
        msg = AgentMessage(
            sender="orchestrator",
            recipient="verification_agent",
            content={"customer_id": state.get("customer_id", "")}
        )
        result = await self.verification.handle(msg)
        ver_out = result.content or {}
        if not isinstance(ver_out, dict):
            ver_out = {"raw": ver_out}
        LOG.info("[ORCHESTRATOR][VERIFICATION] customer=%s keys=%s", state.get("customer_id"), list(ver_out.keys()))
        print(f"[VERIFICATION] customer={state.get('customer_id')} verification_out={ver_out}")
        return {"verification_result": ver_out}

    async def _underwriting_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Call UnderwritingAgent with document-first semantics and anomaly hints."""
        # Handle both dict and LoanFlowState inputs
        if isinstance(state, LoanFlowState):
            state = state.dict()
        sales_out = state.get("sales_result") or {}
        cust_id = state.get("customer_id", "")

        # Parse requested_amount from sales parsed_request if present
        parsed_req = {}
        if isinstance(sales_out, dict):
            parsed_req = sales_out.get("parsed_request") or {}

        requested_amount_raw = parsed_req.get("requested_amount") if isinstance(parsed_req, dict) else None
        requested_amount = self._coerce_amount(requested_amount_raw)

        # Fallback keys
        loan_amount_from_sales = None
        for key in ("loan_amount", "approved_amount", "amount", "offer_amount"):
            try:
                loan_amount_from_sales = self._coerce_amount(sales_out.get(key))
            except Exception:
                loan_amount_from_sales = None
            if loan_amount_from_sales:
                break

        # Pre-approved fallback from DB
        pre_approved_limit = None
        try:
            if cust_id:
                cust = self.db.get_customer(cust_id)
                if cust:
                    pre_approved_limit = self._coerce_amount(
                        cust.get("pre_approved_limit") or cust.get("pre_approved_amount")
                    )
        except Exception as e:
            LOG.debug("Pre-approved limit read failed: %s", e)

        # Choose chosen_amount
        chosen_amount = None
        if requested_amount and requested_amount > 0:
            chosen_amount = requested_amount
            LOG.info("[ORCHESTRATOR] Using requested_amount from Sales: %s for cust=%s", chosen_amount, cust_id)
        elif loan_amount_from_sales and loan_amount_from_sales > 0:
            chosen_amount = loan_amount_from_sales
            LOG.info("[ORCHESTRATOR] Using loan_amount from Sales keys: %s for cust=%s", chosen_amount, cust_id)
        else:
            offer = sales_out.get("offer") if isinstance(sales_out, dict) else None
            offer_max = None
            if isinstance(offer, dict):
                offer_max = self._coerce_amount(offer.get("max_amount") or offer.get("amount"))
            if offer_max and offer_max > 0:
                chosen_amount = offer_max
                LOG.info("[ORCHESTRATOR] Using offer.max_amount: %s for cust=%s", chosen_amount, cust_id)
            elif pre_approved_limit and pre_approved_limit > 0:
                chosen_amount = pre_approved_limit
                LOG.info("[ORCHESTRATOR] Falling back to pre_approved_limit=%s for cust=%s", chosen_amount, cust_id)
            else:
                chosen_amount = 0
                LOG.info("[ORCHESTRATOR] No sales or DB amount found; defaulting loan_amount=0 for cust=%s", cust_id)

        # Tenure
        try:
            tenure = int(
                parsed_req.get("requested_tenure_months") or
                parsed_req.get("tenure") or
                sales_out.get("tenure_months") or
                sales_out.get("tenure") or
                12
            )
        except Exception:
            tenure = 12

        # Require income doc if requested > pre-approved
        requires_income_doc = False
        try:
            if chosen_amount and pre_approved_limit and (float(chosen_amount) > float(pre_approved_limit)):
                requires_income_doc = True
                LOG.info(
                    "[ORCHESTRATOR] requested amount (%s) exceeds pre-approved limit (%s) -> income doc required for cust=%s",
                    chosen_amount, pre_approved_limit, cust_id
                )
        except Exception:
            pass

        print(f"[ORCHESTRATOR] Underwriting invoked for cust={cust_id} loan_amount={chosen_amount} tenure={tenure} (requires_income_doc={requires_income_doc})")
        LOG.info("[ORCHESTRATOR][UNDERWRITING_IN] customer=%s loan_amount=%s tenure=%s requires_income_doc=%s", 
                 cust_id, chosen_amount, tenure, requires_income_doc)

        # Assemble content for underwriting
        content = {
            "customer_id": cust_id,
            "loan_amount": chosen_amount,
            "tenure_months": tenure,
            "sales_parsed_request": parsed_req,
            "pre_approved_limit": pre_approved_limit,
            "requires_income_doc": requires_income_doc,
        }

        # Attach verification uploaded docs
        verification = state.get("verification_result") or {}
        if state.get("salary_slip_path"):
            content["salary_slip_path"] = state.get("salary_slip_path")
            verification["uploaded_doc_name"] = os.path.basename(state.get("salary_slip_path"))
            verification["uploaded_at"] = time.time()
            verification["uploaded_path"] = state.get("salary_slip_path")

        if isinstance(verification.get("uploaded_docs"), (list, tuple)) and verification.get("uploaded_docs"):
            content["uploaded_docs"] = verification.get("uploaded_docs")

        # Document-first salary extraction
        doc_salary = None
        for k in ("monthly_salary_from_underwriting", "monthly_salary_from_docs", "monthly_salary_used", 
                  "monthly_salary", "extracted_monthly_salary"):
            if verification.get(k) not in (None, ""):
                try:
                    doc_salary_val = float(verification.get(k))
                    doc_salary = doc_salary_val
                    break
                except Exception:
                    continue

        try:
            if not doc_salary and state.get("underwriting_result"):
                uw_prev = state.get("underwriting_result") or {}
                for k in ("monthly_salary_used", "monthly_salary", "monthly_salary_from_docs"):
                    if uw_prev.get(k) not in (None, ""):
                        try:
                            doc_salary = float(uw_prev.get(k))
                            break
                        except Exception:
                            continue
        except Exception:
            pass

        if doc_salary:
            content["monthly_salary_from_docs"] = doc_salary
            content["ocr_confidence"] = verification.get("salary_extraction_confidence") or verification.get("ocr_confidence") or 0.0
            content["ocr_matched_line"] = verification.get("ocr_matched_line")
            content["ocr_source"] = verification.get("ocr_source", verification.get("salary_extraction_source"))
            content["income_source_preferred"] = "doc_only"
            LOG.info("[ORCHESTRATOR] Passing doc salary to underwriting for cust=%s salary=%s", cust_id, doc_salary)
        else:
            # Fallback to DB monthly estimate
            try:
                if cust_id:
                    cust = self.db.get_customer(cust_id)
                    if cust:
                        if "monthly_income" in cust and cust.get("monthly_income"):
                            content["monthly_salary_from_db_estimate"] = float(cust.get("monthly_income"))
                        elif cust.get("annual_income"):
                            content["monthly_salary_from_db_estimate"] = float(cust.get("annual_income")) / 12.0
            except Exception:
                pass
            content["income_source_preferred"] = "db_estimate"

        # Anomaly heuristics
        try:
            db_salary = None
            if cust_id:
                cust = self.db.get_customer(cust_id)
                if cust:
                    if cust.get("monthly_income"):
                        db_salary = float(cust.get("monthly_income"))
                    elif cust.get("annual_income"):
                        db_salary = float(cust.get("annual_income")) / 12.0
            
            # Salary mismatch
            if doc_salary and db_salary:
                ratio = max(doc_salary / (db_salary or 1.0), (db_salary or 1.0) / (doc_salary or 1.0))
                if ratio >= 3.0:
                    LOG.warning("[ORCHESTRATOR] Salary mismatch detected (doc vs db) ratio=%.2f -> flagging hint for cust=%s", 
                               ratio, cust_id)
                    content.setdefault("anomaly_hint", {})
                    content["anomaly_hint"]["salary_mismatch"] = {
                        "doc_salary": doc_salary,
                        "db_salary": db_salary,
                        "ratio": ratio,
                        "rule": "doc/db mismatch >= 3x"
                    }
        except Exception:
            pass

        # Low OCR confidence hint
        try:
            ocr_conf = content.get("ocr_confidence") or 0.0
            if doc_salary and float(ocr_conf) < 0.45:
                content.setdefault("anomaly_hint", {})
                content["anomaly_hint"]["low_ocr_confidence"] = float(ocr_conf)
                LOG.info("[ORCHESTRATOR] Low OCR confidence (%.2f) hint attached for cust=%s", float(ocr_conf), cust_id)
        except Exception:
            pass

        if isinstance(verification.get("uploaded_docs"), (list, tuple)) and verification.get("uploaded_docs"):
            content["uploaded_docs"] = verification.get("uploaded_docs")

        # Send to UnderwritingAgent
        msg = AgentMessage(sender="orchestrator", recipient="underwriting_agent", content=content)
        result = await self.underwriting.handle(msg)
        uw_out = result.content or {}

        # Ensure loan_details nested exist
        if "loan_details" not in uw_out:
            loan_details = {}
            loan_details["loan_amount"] = uw_out.get("loan_amount") or chosen_amount or 0
            loan_details["interest_rate"] = uw_out.get("interest_rate") or uw_out.get("rate") or 12.0
            loan_details["tenure_months"] = uw_out.get("tenure_months") or tenure
            loan_details["monthly_emi"] = uw_out.get("monthly_emi") or uw_out.get("emi") or None
            if uw_out.get("application_id"):
                loan_details["application_id"] = uw_out.get("application_id")
            uw_out = {**uw_out, "loan_details": loan_details}

        # Carry requires_income_doc flag
        if requires_income_doc and "requires_income_doc" not in uw_out:
            uw_out["requires_income_doc"] = True

        LOG.info("[ORCHESTRATOR][UNDERWRITING_OUT] customer=%s keys=%s", cust_id, list(uw_out.keys()))
        print(f"[UNDERWRITING] customer={cust_id} underwriting_out={uw_out}")

        return {"underwriting_result": uw_out}

    async def _sanction_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Call SanctionAgent with structured loan_details."""
        # Handle both dict and LoanFlowState inputs
        if isinstance(state, LoanFlowState):
            state = state.dict()
        uw = state.get("underwriting_result") or {}
        decision = (uw.get("decision") or "").lower()
        cust_id = state.get("customer_id", "")

        # Pending salary slip
        if decision in ("needs_salary_slip", "pending_salary_slip"):
            LOG.info("[ORCHESTRATOR] Underwriting requires salary slip; skipping sanction until doc provided.")
            pending = {
                "customer_id": cust_id,
                "decision": "pending_salary_slip",
                "message": "Sanctioning paused: awaiting salary slip and verification.",
                "underwriting_result": uw,
                "generated": False,
            }
            print(f"[SANCTION] customer={cust_id} sanction_payload={pending}")
            return {"sanction_result": pending}

        # Manual review flagged
        if uw.get("flag_for_manual_review"):
            LOG.info("[ORCHESTRATOR] Underwriting flagged manual review; saving snapshot and skipping sanction.")
            manual_record = {
                "timestamp": time.time(),
                "customer_id": cust_id,
                "underwriting_result": uw,
                "sales_result": state.get("sales_result"),
                "verification_result": state.get("verification_result"),
            }
            fname = os.path.join(self.manual_review_dir, f"manual_review_{cust_id}_{int(time.time())}.json")
            try:
                with open(fname, "w", encoding="utf-8") as fh:
                    json.dump(manual_record, fh, indent=2, default=str)
                LOG.info("[ORCHESTRATOR] Saved manual review snapshot: %s", fname)
            except Exception:
                LOG.exception("Failed to write manual review snapshot")

            pending = {
                "customer_id": cust_id,
                "decision": "manual_review_required",
                "message": "Underwriting flagged the application for manual review due to anomalies.",
                "anomalies": uw.get("anomalies_detected", []),
                "underwriting_result": uw,
                "generated": False,
                "manual_review_snapshot": fname,
            }
            print(f"[SANCTION] customer={cust_id} sanction_payload={pending}")
            return {"sanction_result": pending}

        # Surface counterfactual suggestion
        if uw.get("counterfactual"):
            sales_result = state.get("sales_result") or {}
            sales_result["counterfactual"] = uw.get("counterfactual")
            LOG.info("[ORCHESTRATOR] Surface counterfactual to sales_result for cust=%s", cust_id)

        loan_details = uw.get("loan_details") if isinstance(uw, dict) and uw.get("loan_details") else {
            "loan_amount": uw.get("loan_amount"),
            "interest_rate": uw.get("interest_rate"),
            "tenure_months": uw.get("tenure_months"),
            "monthly_emi": uw.get("monthly_emi"),
            "application_id": uw.get("application_id"),
        }

        payload = {
            "customer_id": cust_id,
            "decision": uw.get("decision") or uw.get("status") or "approved",
            "loan_details": loan_details,
            "save_to_disk": True,
        }
        LOG.info("[ORCHESTRATOR][SANCTION_IN] customer=%s loan_details=%s", cust_id, loan_details)
        print(f"[SANCTION] customer={cust_id} sanction_payload={payload}")

        msg = AgentMessage(sender="orchestrator", recipient="sanction_agent", content=payload)
        result = await self.sanction.handle(msg)
        san_out = result.content or {}
        LOG.info("[ORCHESTRATOR][SANCTION_OUT] customer=%s keys=%s", cust_id, list(san_out.keys()))
        print(f"[SANCTION_OUT] customer={cust_id} sanction_out={san_out}")
        return {"sanction_result": san_out}

    def _build_workflow(self) -> StateGraph:
        """Build the LangGraph workflow."""
        # Define the graph
        workflow = StateGraph(dict)

        # Add nodes
        workflow.add_node("sales", self._sales_node)
        workflow.add_node("verification", self._verification_node)
        workflow.add_node("underwriting", self._underwriting_node)
        workflow.add_node("sanction", self._sanction_node)

        # Define the flow
        workflow.set_entry_point("sales")
        workflow.add_edge("sales", "verification")
        workflow.add_edge("verification", "underwriting")
        workflow.add_edge("underwriting", "sanction")
        workflow.add_edge("sanction", END)

        return workflow

    async def run_with_salary_slip(self, state: LoanFlowState, salary_slip_path: str) -> Dict[str, Any]:
        """Upload a salary slip and re-run underwriting+sanction."""
        if not salary_slip_path:
            raise ValueError("salary_slip_path is required for run_with_salary_slip.")
        if not os.path.exists(salary_slip_path):
            raise FileNotFoundError(f"salary_slip_path not found: {salary_slip_path}")

        with open(salary_slip_path, "rb") as fh:
            file_bytes = fh.read()

        # Record minimal verification info
        state.verification_result = state.verification_result or {}
        state.verification_result["customer_id"] = state.customer_id
        state.verification_result["verification_status"] = state.verification_result.get("verification_status", "passed")
        state.verification_result["message"] = "Salary slip uploaded (handled by Underwriting agent for OCR)."
        state.verification_result["uploaded_doc_name"] = os.path.basename(salary_slip_path)
        state.verification_result["uploaded_at"] = time.time()
        state.salary_slip_path = salary_slip_path

        uw_payload = {
            "customer_id": state.customer_id,
            "loan_amount": (state.sales_result or {}).get("parsed_request", {}).get("requested_amount") if (state.sales_result or {}).get("parsed_request") else None,
            "tenure_months": (state.sales_result or {}).get("parsed_request", {}).get("requested_tenure_months") if (state.sales_result or {}).get("parsed_request") else None,
            "uploaded_docs": [{"doc_type": "salary_slip", "file_name": os.path.basename(salary_slip_path), "file_bytes": file_bytes}],
            "sales_parsed_request": (state.sales_result or {}).get("parsed_request") or {},
            "pre_approved_limit": (state.sales_result or {}).get("offer", {}).get("max_amount") if (state.sales_result or {}).get("offer") else None,
            "requires_income_doc": True,
        }

        msg = AgentMessage(sender="orchestrator", recipient="underwriting_agent", content=uw_payload)
        result = await self.underwriting.handle(msg)
        uw_out = result.content or {}

        # Attach salary slip meta to verification_result
        if "monthly_salary" in uw_out and uw_out.get("monthly_salary") is not None:
            state.verification_result["monthly_salary_from_underwriting"] = uw_out.get("monthly_salary")
            state.verification_result["salary_extraction_confidence"] = uw_out.get("ocr_confidence", 0.0)
            state.verification_result["ocr_matched_line"] = uw_out.get("ocr_matched_line")
            state.verification_result["ocr_source"] = uw_out.get("ocr_source")

        decision = (uw_out.get("decision") or "").lower()
        if decision in ("needs_salary_slip", "pending_salary_slip"):
            return {
                "verification_result": state.verification_result,
                "underwriting_result": uw_out,
                "sanction_result": {
                    "decision": "pending_salary_slip",
                    "message": "Underwriting still requests salary slip or failed extraction."
                }
            }

        state.underwriting_result = uw_out
        san_out_wrapper = await self._sanction_node(state.dict())
        san_out = san_out_wrapper.get("sanction_result") or {}
        return {
            "verification_result": state.verification_result,
            "underwriting_result": uw_out,
            "sanction_result": san_out
        }

    async def run(self, user_text: str, customer_id: str, salary_slip_path: Optional[str] = None) -> Dict[str, Any]:
        """Run the workflow through LangGraph."""
        # Prepare initial state
        initial_state = {
            "user_text": user_text,
            "customer_id": customer_id,
            "salary_slip_path": salary_slip_path or "",
            "sales_result": {},
            "verification_result": {},
            "underwriting_result": {},
            "sanction_result": {},
            "auto_approve": False,
        }

        # If salary slip provided, handle separately
        if salary_slip_path:
            state = LoanFlowState(user_text=user_text, customer_id=customer_id)
            # Run sales & verification first
            sales_msg = AgentMessage(
                sender="orchestrator",
                recipient="sales_agent",
                content={"customer_id": customer_id, "user_input": user_text}
            )
            sales_res = await self.sales.handle(sales_msg)
            state.sales_result = sales_res.content or {}

            ver_msg = AgentMessage(
                sender="orchestrator",
                recipient="verification_agent",
                content={"customer_id": customer_id}
            )
            ver_res = await self.verification.handle(ver_msg)
            state.verification_result = ver_res.content or {}

            return await self.run_with_salary_slip(state, salary_slip_path)

        # Run the workflow
        config = {"configurable": {"thread_id": f"{customer_id}_{int(time.time())}"}}
        
        final_state = None
        async for event in self.app.astream(initial_state, config):
            final_state = event
        
        # Extract results from final state
        if final_state:
            # Get the last node's output
            last_node_key = list(final_state.keys())[-1]
            last_node_state = final_state[last_node_key]
            
            return {
                "sales_result": last_node_state.get("sales_result", {}),
                "verification_result": last_node_state.get("verification_result", {}),
                "underwriting_result": last_node_state.get("underwriting_result", {}),
                "sanction_result": last_node_state.get("sanction_result", {}),
            }
        
        return {
            "sales_result": {},
            "verification_result": {},
            "underwriting_result": {},
            "sanction_result": {},
        }
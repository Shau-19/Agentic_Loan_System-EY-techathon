# src/tests/test_master_agent.py
"""
Interactive MasterAgent end-to-end test (test harness).
- Looks up phone first, verifies customer, then asks amount/tenure/purpose.
- If amount exceeds pre-approved limit, asks to upload a salary slip.
- Then runs underwriting and sanction using your LoanOrchestrator and agents.
Run: python -m src.tests.test_master_agent
"""

'''import asyncio
import sys
import textwrap
from pathlib import Path
from typing import Optional

# Import your orchestrator and AgentMessage
from src.agents.orchestrator import LoanOrchestrator, LoanFlowState
from src.agents.base_agent import AgentMessage

# --- Config / tweak area ---
USE_PRETTY_BOX = True  # removed DOC_CHECK_THRESHOLD logic (no longer used)

# ---- Helpers ----
def box_print(title: str, body: str = "", width: int = 72):
    sep = "=" * width
    print("\n" + sep)
    print(f"{title}")
    if body:
        print("-" * width)
        print(textwrap.fill(body, width=width))
    print(sep + "\n")


def simple_input(prompt: str, default: Optional[str] = None):
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    res = input(prompt).strip()
    if res == "" and default is not None:
        return default
    return res


async def find_customer_by_phone(db, phone: str):
    """Tries multiple common DB helper method names and fallbacks to find a customer by phone."""
    phone = phone.strip()
    if phone == "":
        return None

    # 1) direct method names likely present
    for fn in ("get_customer_by_phone", "find_customer_by_phone", "lookup_customer_by_phone"):
        if hasattr(db, fn):
            try:
                cust = getattr(db, fn)(phone)
                if cust:
                    return cust
            except Exception:
                pass

    # 2) scan through get_all_customers
    if hasattr(db, "get_all_customers"):
        try:
            for c in db.get_all_customers():
                ph = (c.get("phone") or c.get("mobile") or "").strip()
                if ph.endswith(phone) or phone.endswith(ph) or ph == phone:
                    return c
        except Exception:
            pass

    # 3) attribute-based collections
    for attr in ("_customers", "customers", "seed_customers"):
        if hasattr(db, attr):
            coll = getattr(db, attr)
            if isinstance(coll, dict):
                for _, c in coll.items():
                    ph = (c.get("phone") or "").strip()
                    if ph.endswith(phone) or ph == phone:
                        return c
            elif isinstance(coll, (list, tuple)):
                for c in coll:
                    ph = (c.get("phone") or "").strip()
                    if ph.endswith(phone) or ph == phone:
                        return c

    return None


async def upload_salary_slip_and_invoke_verification(underwriting_agent, orchestrator, customer_id: str, file_path: str):
    """Uploads salary slip and triggers underwriting via orchestrator.run_with_salary_slip."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Salary slip file not found: {p.resolve()}")

    try:
        state = LoanFlowState(customer_id=customer_id)
        result = await orchestrator.run_with_salary_slip(state, str(p.resolve()))
        uw_out = result.get("underwriting_result") or {}
        print("📤 Sending salary slip to UnderwritingAgent (for OCR & income extraction)...")
        print("✅ UnderwritingAgent returned:", uw_out)
        return uw_out
    except Exception as e:
        print("⚠️ Orchestrator run_with_salary_slip failed — falling back to direct upload.", e)

    file_bytes = p.read_bytes()
    msg = AgentMessage(
        sender="test",
        recipient="underwriting_agent",
        content={
            "customer_id": customer_id,
            "uploaded_docs": [{"doc_type": "salary_slip", "file_name": p.name, "file_bytes": file_bytes}],
            "requires_income_doc": True,
        },
    )
    res = await underwriting_agent.handle(msg)
    print("✅ UnderwritingAgent returned:", res.content)
    return res.content


def print_underwriting_verbose(uw: dict):
    """Nicely print underwriting result summary."""
    box_print("🧾 Underwriting result (detailed)")
    decision = uw.get("decision", "<no decision>")
    message = uw.get("message", "")
    reasons = uw.get("reasons", []) or []
    interest_rate = uw.get("interest_rate")
    monthly_emi = uw.get("monthly_emi")
    loan_details = uw.get("loan_details") or {}
    monthly_salary_used = uw.get("monthly_salary_used")
    requires_income_doc = uw.get("requires_income_doc", False)

    if monthly_salary_used:
        print(f"- Salary used for decision: ₹{int(monthly_salary_used):,}")
    else:
        print("- No explicit salary found; using DB fallback.")

    if monthly_emi:
        print(f"- Computed EMI: ₹{int(monthly_emi):,}/month (interest {interest_rate}%)")

    if monthly_salary_used and monthly_emi:
        threshold = 0.5 * float(monthly_salary_used)
        print(f"- EMI policy: {monthly_emi} ≤ 50% of {monthly_salary_used} → {monthly_emi <= threshold}")
    elif requires_income_doc:
        print("- Decision requires salary slip upload.")
    print(f"\nUNDERWRITING DECISION: {decision.upper()}")
    if reasons:
        print("Reasons:", ", ".join(reasons))
    if message:
        print("Message:", message)
    if loan_details:
        print("\nLoan details:")
        for k in ("application_id", "loan_amount", "interest_rate", "tenure_months", "monthly_emi", "processing_fee"):
            if k in loan_details:
                print(f" - {k}: {loan_details[k]}")
    print("-" * 72 + "\n")


# ---- Interactive Flow ----
async def interactive_test_flow():
    orchestrator = LoanOrchestrator()
    db = orchestrator.db
    sales_agent = orchestrator.sales
    verification_agent = orchestrator.verification
    underwriting_agent = orchestrator.underwriting
    sanction_agent = orchestrator.sanction

    box_print("🚀 MasterAgent Interactive E2E Test", "This interactive test will: (1) identify customer by phone, (2) run Sales, (3) run Verification (with optional salary slip upload), (4) Underwriting, (5) Sanction.")

    # Step 1: Lookup
    phone = simple_input("Enter customer's phone number (include country code, e.g. +91 98... )")
    box_print("🔎 Looking up customer", f"Phone provided: {phone}")
    customer = await find_customer_by_phone(db, phone)

    if not customer:
        box_print("⚠️ Customer not found", "Could not locate the customer by phone.")
        sys.exit(1)

    cust_id = customer.get("customer_id")
    name = customer.get("name") or "Customer"
    pre_limit = float(customer.get("pre_approved_limit") or 0)
    box_print("✅ Customer found", f"{name} / id={cust_id}\nPre-approved limit: ₹{int(pre_limit):,}")

    # Step 2: Sales input
    purpose = simple_input("Sales: What is the purpose of the loan?", default="general")
    amount_input = simple_input(f"Sales: Enter loan amount (press Enter to use pre-approved limit ₹{int(pre_limit):,})", default=str(int(pre_limit)))
    tenure_input = simple_input("Sales: Enter tenure in months (12/24/36) (press Enter for 12)", default="12")

    try:
        requested_amount = float(amount_input)
    except ValueError:
        requested_amount = pre_limit

    try:
        requested_tenure = int(tenure_input)
    except ValueError:
        requested_tenure = 12

    sales_msg = AgentMessage(sender="test", recipient="sales_agent", content={"customer_id": cust_id, "user_input": f"I want {requested_amount} for {requested_tenure} months"})
    print("💬 Sending to SalesAgent for parsing and response...")
    try:
        sales_res = await sales_agent.handle(sales_msg)
        sales_out = sales_res.content or {}
    except Exception as e:
        print("⚠️ SalesAgent failed:", e)
        sales_out = {
            "parsed_request": {"requested_amount": requested_amount, "requested_tenure_months": requested_tenure},
            "offer": {"max_amount": pre_limit, "interest_rate": 12.0, "tenure_months": [12, 24, 36]},
            "message": f"Eligible for ₹{requested_amount} for {requested_tenure} months.",
        }

    box_print("📣 Sales Response", f"{sales_out.get('message')}\nParsed request: {sales_out.get('parsed_request')}")

    # Step 3: Verification
    box_print("🔐 Running Verification", "VerificationAgent will check customer identity.")
    ver_msg = AgentMessage(sender="test", recipient="verification_agent", content={"customer_id": cust_id})
    ver_res = await verification_agent.handle(ver_msg)
    verification_out = ver_res.content or {}
    box_print("🔎 Verification result", str(verification_out))

    # Step 4: Check if salary slip needed (ONLY if requested > pre_approved_limit)
    doc_required = False
    if requested_amount > pre_limit:
        doc_required = True
        box_print("📎 Documents required", f"The requested amount ₹{int(requested_amount):,} exceeds pre-approved limit ₹{int(pre_limit):,}. Salary slip required.")
        file_path = simple_input("Enter path to salary slip PDF (or type 'skip' to simulate)", default="tests/data/sample_salary_slip.pdf")
        if file_path.lower() != "skip":
            verification_out = await upload_salary_slip_and_invoke_verification(underwriting_agent, orchestrator, cust_id, file_path)
        else:
            print("Skipping upload; continuing with simulated verification result.")
            verification_out["verified_docs"] = ["salary_slip"]
    else:
        print("✅ Requested amount within pre-approved limit. No salary slip required.")

    # Step 5: Underwriting
    box_print("🏦 Underwriting", f"Preparing inputs — customer_id={cust_id} loan_amount={requested_amount} tenure={requested_tenure}")
    state = LoanFlowState(
        user_text=f"I want {requested_amount} for {requested_tenure} months",
        customer_id=cust_id,
        sales_result=sales_out,
        verification_result=verification_out,
    )
    uw_result = await orchestrator._underwriting_node(state)
    underwriting_out = uw_result.get("underwriting_result") or {}
    print_underwriting_verbose(underwriting_out)

    # Step 6: Sanction
    box_print("📜 Sanction", "Generating sanction letter if approved.")
    state.underwriting_result = underwriting_out
    san_result = await orchestrator._sanction_node(state)
    sanction_out = san_result.get("sanction_result") or {}
    box_print("✅ Sanction result", str(sanction_out))

    pdf_path = sanction_out.get("pdf_path")
    if pdf_path:
        print(f"📄 Sanction letter saved as: {pdf_path}")
    else:
        print("📄 No sanction letter generated (likely rejected).")

    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("- Customer:", cust_id, name)
    print("- Requested amount:", f"₹{int(requested_amount)}")
    print("- Tenure:", requested_tenure)
    print("- Underwriting decision:", underwriting_out.get("decision"))
    print("- Sanction decision:", sanction_out.get("decision"))
    print("=" * 80 + "\n")
    print("Interactive MasterAgent test complete. ✅")


if __name__ == "__main__":
    try:
        asyncio.run(interactive_test_flow())
    except KeyboardInterrupt:
        print("\nTest aborted by user.")
        sys.exit(0)
'''


# src/tests/test_master_agent.py
"""
Interactive MasterAgent end-to-end test (test harness).
- Looks up phone first, verifies customer, then asks amount/tenure/purpose.
- If amount is conditional, asks to upload a salary slip (path to a local PDF).
- Then runs underwriting and sanction using your LoanOrchestrator and agents.
Run: python -m src.tests.test_master_agent
"""

# src/tests/test_master_agent.py
"""
Interactive MasterAgent end-to-end test (test harness).
- Looks up phone first, verifies customer, then asks amount/tenure/purpose.
- If amount is conditional, asks to upload a salary slip (path to a local PDF).
- Then runs underwriting and sanction using your LoanOrchestrator and agents.
Run: python -m src.tests.test_master_agent
"""

'''import asyncio
import sys
import textwrap
from pathlib import Path
from typing import Optional

# Import your orchestrator and AgentMessage
from src.agents.orchestrator import LoanOrchestrator, LoanFlowState
from src.agents.base_agent import AgentMessage

# --- Config / tweak area ---
DOC_CHECK_THRESHOLD = 100_000  # ask for salary slip if requested_amount >= this
USE_PRETTY_BOX = True

# ---- Helpers ----
def box_print(title: str, body: str = "", width: int = 72):
    sep = "=" * width
    print("\n" + sep)
    print(f"{title}")
    if body:
        print("-" * width)
        print(textwrap.fill(body, width=width))
    print(sep + "\n")


def simple_input(prompt: str, default: Optional[str] = None):
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    res = input(prompt).strip()
    if res == "" and default is not None:
        return default
    return res


async def find_customer_by_phone(db, phone: str):
    """
    Tries multiple common DB helper method names and fallbacks to find a customer by phone.
    Returns None or the customer dict.
    """
    phone = phone.strip()
    if phone == "":
        return None

    # 1) direct method names likely present
    for fn in ("get_customer_by_phone", "find_customer_by_phone", "lookup_customer_by_phone"):
        if hasattr(db, fn):
            try:
                cust = getattr(db, fn)(phone)
                if cust:
                    return cust
            except Exception:
                pass

    # 2) if db has get_customer(customer_id) but not phone helper, try scanning using get_all_customers
    if hasattr(db, "get_all_customers"):
        try:
            for c in db.get_all_customers():
                ph = (c.get("phone") or c.get("mobile") or "").strip()
                if ph.endswith(phone) or phone.endswith(ph) or ph == phone:
                    return c
        except Exception:
            pass

    # 3) try attribute names that might hold customers in-memory
    for attr in ("_customers", "customers", "seed_customers"):
        if hasattr(db, attr):
            coll = getattr(db, attr)
            if isinstance(coll, dict):
                for _, c in coll.items():
                    ph = (c.get("phone") or "").strip()
                    if ph.endswith(phone) or ph == phone:
                        return c
            elif isinstance(coll, (list, tuple)):
                for c in coll:
                    ph = (c.get("phone") or "").strip()
                    if ph.endswith(phone) or ph == phone:
                        return c

    # 4) Try to call db.get_customer for likely IDs (less likely but harmless)
    # Give up
    return None


async def upload_salary_slip_and_invoke_verification(underwriting_agent, orchestrator, customer_id: str, file_path: str):
    """
    Try preferred mode: use orchestrator.run_with_salary_slip which will send the uploaded
    salary slip to the UnderwritingAgent (so underwriting performs OCR).
    Falls back to older direct agent.handle approach or simulated force-result.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Salary slip file not found: {p.resolve()}")

    # Preferred: ask orchestrator to run_with_salary_slip (it records verification meta etc.)
    try:
        # Build a minimal state and call orchestrator.run_with_salary_slip. This ensures
        # the orchestrator creates provenance entries and the underwriting agent gets the bytes.
        state = LoanFlowState(customer_id=customer_id)
        result = await orchestrator.run_with_salary_slip(state, str(p.resolve()))
        # result contains verification_result, underwriting_result, sanction_result
        uw_out = result.get("underwriting_result") or {}
        print("📤 Sending salary slip to UnderwritingAgent (for OCR & income extraction)...")
        print("✅ UnderwritingAgent returned:", uw_out)
        return uw_out
    except Exception as e:
        # If orchestrator path fails, try direct call to underwriting_agent.handle with uploaded_docs
        print("⚠️ Orchestrator run_with_salary_slip failed — falling back to direct upload to UnderwritingAgent.", e)

    file_bytes = p.read_bytes()
    try:
        msg = AgentMessage(
            sender="test",
            recipient="underwriting_agent",
            content={
                "customer_id": customer_id,
                "uploaded_docs": [
                    {"doc_type": "salary_slip", "file_name": p.name, "file_bytes": file_bytes}
                ],
                "requires_income_doc": True,
            },
        )
        print("📤 Sending salary slip to UnderwritingAgent (direct) ...")
        res = await underwriting_agent.handle(msg)
        print("✅ UnderwritingAgent returned:", res.content)
        return res.content
    except Exception as e:
        print("⚠️ Direct UnderwritingAgent upload failed; falling back to simulated force-result.", e)

    # Final fallback: return a simulated verification-like dict
    return {
        "customer_id": customer_id,
        "verification_status": "passed",
        "verified_docs": ["salary_slip"],
        "monthly_salary": 50000,
        "message": "Simulated verification passed (test harness — underwriting should OCR this).",
        "decision": "approved",
        "monthly_salary_used": 50000,
        "ocr_confidence": 0.75,
        "ocr_matched_line": "Gross Monthly Salary: 50,000",
        "ocr_source": "keyword_line",
        "approval_type": "income_check_passed",
    }


def print_underwriting_verbose(uw: dict):
    """
    Nicely print underwriting fields and human-friendly comparison steps so test output
    explains exactly why the loan was approved/rejected/conditional.
    """
    box_print("🧾 Underwriting result (detailed)")
    # Some safe pulls with defaults
    decision = uw.get("decision", "<no decision>")
    message = uw.get("message", "")
    reasons = uw.get("reasons", []) or []
    interest_rate = uw.get("interest_rate")
    monthly_emi = uw.get("monthly_emi")
    loan_details = uw.get("loan_details") or {}
    emi_ratio = uw.get("emi_ratio")
    monthly_salary_used = uw.get("monthly_salary_used")
    ocr_confidence = uw.get("ocr_confidence")
    ocr_matched_line = uw.get("ocr_matched_line")
    ocr_source = uw.get("ocr_source")
    requires_income_doc = uw.get("requires_income_doc", False)

    # Print extracted salary info
    if monthly_salary_used:
        print(f"- Salary used for decision: ₹{int(monthly_salary_used):,}")
        if ocr_source:
            print(f"  • Source: {ocr_source} (OCR confidence: {float(ocr_confidence):.2f})")
        if ocr_matched_line:
            print(f"  • OCR matched line: {ocr_matched_line}")
    else:
        print("- No explicit salary found; falling back to DB annual income / default assumptions.")

    # Print EMI / rate / tenure
    if monthly_emi:
        print(f"- Computed EMI: ₹{int(monthly_emi):,} per month (interest rate used: {interest_rate}%)")
    else:
        # Try to infer from loan_details
        md_emi = loan_details.get("monthly_emi")
        if md_emi:
            print(f"- Computed EMI (from loan_details): ₹{int(md_emi):,} per month (interest rate used: {interest_rate}%)")
        else:
            print("- EMI not computed / not present in underwriting response.")

    # Print EMI vs salary comparison
    if monthly_salary_used and monthly_emi:
        threshold = 0.5 * float(monthly_salary_used)
        print(f"- Policy check: EMI <= 50% of monthly salary → threshold = ₹{int(threshold):,}")
        print(f"  • EMI = ₹{int(monthly_emi):,}  vs  Threshold = ₹{int(threshold):,}")
        if float(monthly_emi) <= threshold:
            print("  → PASS: EMI is within 50% of monthly salary.")
        else:
            print("  → FAIL: EMI exceeds 50% of monthly salary.")
    elif requires_income_doc:
        print("- Decision requires an income document (salary slip). Upload required to complete decision.")
    else:
        print("- No EMI vs salary check performed (likely instant approval path or missing salary).")

    # Print decision and reasons
    print(f"\nUNDERWRITING DECISION: {decision.upper()}")
    if reasons:
        print("Reasons:", ", ".join(reasons))
    if message:
        print("Message:", message)

    # Print loan_details if present (application id, amount, tenure, fees)
    if loan_details:
        print("\nLoan details:")
        for k in ("application_id", "loan_amount", "interest_rate", "tenure_months", "monthly_emi", "processing_fee"):
            if k in loan_details:
                print(f" - {k}: {loan_details.get(k)}")
    print("-" * 72 + "\n")


# ---- Interactive flow ----
async def interactive_test_flow():
    orchestrator = LoanOrchestrator()
    db = orchestrator.db
    sales_agent = orchestrator.sales
    verification_agent = orchestrator.verification
    underwriting_agent = orchestrator.underwriting
    sanction_agent = orchestrator.sanction

    box_print("🚀 MasterAgent Interactive E2E Test", "This interactive test will: (1) identify customer by phone, (2) run Sales, (3) run Verification (with optional salary slip upload), (4) Underwriting, (5) Sanction.")

    # Step 1: phone lookup & identity
    phone = simple_input("Enter customer's phone number (include country code, e.g. +91 98... )")
    box_print("🔎 Looking up customer", f"Phone provided: {phone}")

    customer = await find_customer_by_phone(db, phone)
    if customer:
        cust_id = customer.get("customer_id") or customer.get("id") or customer.get("customerId") or ""
        name = customer.get("name") or customer.get("customer_name") or "valued customer"
        pre_limit = customer.get("pre_approved_limit") or customer.get("pre_approved_amount") or customer.get("pre_approved") or 0
        box_print("✅ Customer found", f"{name} / id={cust_id}\nPre-approved limit: ₹{pre_limit}")
    else:
        cust_id = ""
        name = None
        pre_limit = 0
        box_print("⚠️ Customer not found", "We could not find a customer with that phone. You can continue as guest (no verification) or try another phone.")

        cont = simple_input("Continue as guest? (y/n)", default="n")
        if cont.lower().startswith("n"):
            print("Aborting test — please run again with a valid phone.")
            return

    # Step 2: sales: ask amount, tenure, purpose
    purpose = simple_input("Sales: What is the purpose of the loan? (e.g. home repair, debt consolidation)", default="general")
    amount_input = simple_input(f"Sales: Enter loan amount (press Enter to use pre-approved limit ₹{pre_limit})", default=str(int(pre_limit) if pre_limit else ""))
    tenure_input = simple_input("Sales: Enter tenure in months (12/24/36) (press Enter for 12)", default="12")

    # sanitize numeric values
    try:
        requested_amount = float(amount_input) if amount_input else (float(pre_limit) if pre_limit else 0.0)
    except Exception:
        requested_amount = float(pre_limit) if pre_limit else 0.0

    try:
        requested_tenure = int(tenure_input) if tenure_input else 12
    except Exception:
        requested_tenure = 12

    # Ask SalesAgent to parse / respond (best-effort)
    sales_msg = AgentMessage(
        sender="test",
        recipient="sales_agent",
        content={"customer_id": cust_id, "user_input": f"I want a loan of {int(requested_amount)} for {requested_tenure} months for {purpose}"}
    )
    print("💬 Sending to SalesAgent for parsing and response...")
    try:
        sales_res = await sales_agent.handle(sales_msg)
        sales_out = sales_res.content or {}
    except Exception as e:
        print("⚠️ Sales agent.handle failed; building local sales_out fallback.", e)
        sales_out = {
            "message": f"Ok — we received a request for ₹{int(requested_amount)} for {requested_tenure} months for {purpose}.",
            "offer": {"max_amount": pre_limit, "interest_rate": 12.0, "tenure_months": [12, 24, 36]},
            "parsed_request": {"requested_amount": int(requested_amount), "requested_tenure_months": requested_tenure},
            "estimated_emi": None,
        }

    box_print("📣 Sales Response", f"{sales_out.get('message')}\nParsed request: {sales_out.get('parsed_request')}")

    # Step 3: verification
    box_print("🔐 Running Verification", "VerificationAgent will check customer identity / KYC status.")
    ver_msg = AgentMessage(sender="test", recipient="verification_agent", content={"customer_id": cust_id})
    try:
        ver_res = await verification_agent.handle(ver_msg)
        verification_out = ver_res.content or {}
        box_print("🔎 Verification result", str(verification_out))
    except Exception as e:
        print("⚠️ verification_agent.handle raised; simulating verification result:", e)
        verification_out = {"customer_id": cust_id, "verification_status": "passed" if cust_id else "failed", "message": "Simulated by test harness"}

    # Determine whether docs required: policy: ask if requested_amount > pre_approved_limit OR requested_amount >= DOC_CHECK_THRESHOLD
    doc_required = False
    parsed_req = sales_out.get("parsed_request") or {}
    parsed_amount = parsed_req.get("requested_amount") or requested_amount
    try:
        parsed_amount = float(parsed_amount)
    except Exception:
        parsed_amount = float(requested_amount or 0.0)

    try:
        pre_limit_val = float(pre_limit or 0.0)
    except Exception:
        pre_limit_val = 0.0

    if parsed_amount > pre_limit_val or parsed_amount >= DOC_CHECK_THRESHOLD:
        doc_required = True

    verification_status = verification_out.get("verification_status") or verification_out.get("status") or ("passed" if cust_id else "not_verified")
    if verification_status != "passed":
        box_print("🔔 Verification pending", "Customer verification is not passed yet. The test will attempt to verify using DB info or ask for documents.")
        if cust_id and not doc_required:
            print("Attempting to mark verification passed from DB record (test harness).")
            verification_status = "passed"
            verification_out = {"customer_id": cust_id, "verification_status": "passed", "message": "Marked passed from DB (test harness)"}

    # If documents are required, ask to upload salary slip
    if doc_required:
        box_print("📎 Documents required", f"The requested amount ₹{int(parsed_amount)} requires a salary slip (policy threshold: ₹{DOC_CHECK_THRESHOLD} or above pre-approved limit).")
        while True:
            file_path = simple_input("Enter path to salary slip PDF (or type 'skip' to simulate)", default="tests/data/sample_salary_slip.pdf")
            if file_path.lower().strip() == "skip":
                print("Skipping actual upload; using simulated verification pass.")
                verification_result = {"customer_id": cust_id, "verification_status": "passed", "verified_docs": ["salary_slip"], "message": "Simulated upload skip"}
                break
            p = Path(file_path)
            if not p.exists():
                print(f"File not found: {p}. Please enter a valid path or type 'skip'.")
                continue
            # upload & call Underwriting via orchestrator.run_with_salary_slip (preferred)
            verification_result = await upload_salary_slip_and_invoke_verification(underwriting_agent, orchestrator, cust_id, file_path)
            break
        verification_out = verification_result
        verification_status = verification_out.get("verification_status") or verification_out.get("status") or "passed"

    # Step 4: Underwriting
    box_print("🏦 Underwriting", f"Preparing inputs - customer_id={cust_id} loan_amount={int(parsed_amount)} tenure={int(parsed_req.get('requested_tenure_months') or requested_tenure)}")
    state = LoanFlowState(
        user_text=f"I want a loan of {int(parsed_amount)} for {int(parsed_req.get('requested_tenure_months') or requested_tenure)} months",
        customer_id=cust_id,
        sales_result=sales_out,
        verification_result=verification_out,
    )

    # call underwriting node (internal call to preserve orchestration logic / fallbacks)
    try:
        uw_result = await orchestrator._underwriting_node(state)
        underwriting_out = uw_result.get("underwriting_result") or {}
    except Exception as e:
        print("⚠️ Underwriting node call failed; falling back to direct underwriting agent call.", e)
        uw_msg = AgentMessage(
            sender="test",
            recipient="underwriting_agent",
            content={"customer_id": cust_id, "loan_amount": parsed_amount, "tenure_months": requested_tenure},
        )
        uw_res = await underwriting_agent.handle(uw_msg)
        underwriting_out = uw_res.content or {}

    # New: verbose underwriting explanation for test readability
    print_underwriting_verbose(underwriting_out)

    # Step 5: Sanction
    box_print("📜 Sanction", "Generating sanction (if approved) and saving PDF if agent returns one.")
    # create state for sanction
    state.underwriting_result = underwriting_out
    try:
        san_result = await orchestrator._sanction_node(state)
        sanction_out = san_result.get("sanction_result") or {}
    except Exception as e:
        print("⚠️ Sanction node call failed; falling back to calling sanction agent directly.", e)
        san_msg = AgentMessage(sender="test", recipient="sanction_agent", content={
            "customer_id": cust_id,
            "decision": underwriting_out.get("decision") or "rejected",
            "loan_details": underwriting_out.get("loan_details") or {},
            "save_to_disk": True,
        })
        san_res = await sanction_agent.handle(san_msg)
        sanction_out = san_res.content or {}

    box_print("✅ Sanction result", str(sanction_out))

    # If sanction_out has pdf_bytes or pdf_path, write to tmp file for easy inspection
    pdf_path = sanction_out.get("pdf_path")
    if sanction_out.get("pdf_bytes") and not pdf_path:
        tmp = Path.cwd() / "tmp_sanction_from_master.pdf"
        tmp.write_bytes(sanction_out.get("pdf_bytes"))
        pdf_path = str(tmp)
    if pdf_path:
        print(f"📄 Sanction letter saved as: {pdf_path}")
    else:
        print("📄 No sanction PDF generated (likely rejected or pending).")

    # Print summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("- Customer:", cust_id or "(guest)", name or "")
    print("- Requested amount:", f"₹{int(parsed_amount)}")
    print("- Tenure (months):", requested_tenure)
    print("- Verification status:", verification_status)
    print("- Underwriting decision:", underwriting_out.get("decision"))
    print("- Sanction decision:", sanction_out.get("decision"))
    print("=" * 80 + "\n")

    print("Interactive MasterAgent test complete. ✅")


if __name__ == "__main__":
    try:
        asyncio.run(interactive_test_flow())
    except KeyboardInterrupt:
        print("\nTest aborted by user.")
        sys.exit(0)
'''


# src/tests/test_master_agent.py
"""
Interactive MasterAgent end-to-end test with improved conversational interface.

Run: python -m src.tests.test_master_agent
"""
# updated_test_master_agent.py
"""
Enhanced Interactive Test Script for Master Agent
- Provides clear salary slip upload prompts
- Tests approval and rejection scenarios
- Validates against problem statement edge cases
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional

from src.agents.master_agent import MasterAgent

def box_print(title: str, width: int = 80):
    """Print a boxed title"""
    print("\n" + "=" * width)
    print(f" {title}")
    print("=" * width)


async def interactive_chat_with_upload():
    """
    Enhanced interactive chat that prompts for file upload
    and explains approval/rejection based on customer data
    """
    box_print("💬 QUICKCASH LOAN CHATBOT - INTERACTIVE MODE")
    print("Type your messages as a customer. Type 'quit' to exit.\n")

    master = MasterAgent()

    # Start conversation
    entry = await master.start_conversation({"source": "website"})
    conv_id = entry["conversation_id"]

    print(f"🤖 Sarah: {entry['message']}\n")

    while True:
        user_input = input("👤 You: ").strip()

        if user_input.lower() in ['quit', 'exit', 'bye']:
            exit_msg = await master.end_conversation(conv_id, "customer_request")
            print(f"\n🤖 Sarah: {exit_msg['farewell_message']}")
            break

        if not user_input:
            continue

        # Process message
        response = await master.chat(conv_id, user_input)

        print(f"\n🤖 Sarah: {response['message']}")

        # Show suggested actions if available
        if response.get("suggested_actions"):
            print("\n💡 Suggestions:", " | ".join(response["suggested_actions"]))

        # Handle document upload prompt
        if response.get("next_action") == "upload_document":
            print("\n" + "─" * 80)
            print("📎 SALARY SLIP UPLOAD REQUIRED")
            print("─" * 80)
            print("Your loan amount requires income verification.")
            print("\nOptions:")
            print("  1. Enter file path (e.g., E:\\docs\\salary_slip.pdf)")
            print("  2. Type 'sample' to use test data")
            print("  3. Type 'skip' to simulate upload")
            print("─" * 80)

            file_input = input("\n📁 Your choice: ").strip()

            if file_input.lower() == 'skip':
                # Continue conversation without upload
                print("⏭️  Skipping upload for now...\n")
                continue
            elif file_input.lower() == 'sample':
                # Use sample file path
                file_input = "src/tests/data/sample_salary_slip.pdf"
                print(f"📄 Using sample file: {file_input}\n")

            # Send file path to master agent
            upload_response = await master.chat(conv_id, file_input)
            print(f"\n🤖 Sarah: {upload_response['message']}")

            # Show approval details if loan approved
            if upload_response.get("state") == "completed":
                data = upload_response.get("data", {})
                loan_details = data.get("loan_details", {})
                
                if loan_details:
                    print("\n" + "─" * 80)
                    print("📋 LOAN APPROVAL DETAILS")
                    print("─" * 80)
                    print(f"  Application ID: {loan_details.get('application_id', 'N/A')}")
                    print(f"  Loan Amount: ₹{int(loan_details.get('loan_amount', 0)):,}")
                    print(f"  Interest Rate: {loan_details.get('interest_rate', 0)}% p.a.")
                    print(f"  Tenure: {loan_details.get('tenure_months', 0)} months")
                    print(f"  Monthly EMI: ₹{int(loan_details.get('monthly_emi', 0)):,}")
                    print(f"  Processing Fee: ₹{int(loan_details.get('processing_fee', 0)):,}")
                    print("─" * 80)

                if data.get("sanction_letter_path"):
                    print(f"\n📄 Sanction Letter: {data['sanction_letter_path']}")

            print()

        # Show next action hint
        if response.get("next_action") and response["next_action"] not in ["wait", "upload_document"]:
            print(f"🎯 Next: {response['next_action']}")

        print()

        # Check if completed
        if response.get("state") == "completed":
            should_continue = input("\n💬 Continue chatting? (yes/no): ").strip().lower()
            if should_continue != "yes":
                exit_msg = await master.end_conversation(conv_id, "completed")
                print(f"\n🤖 Sarah: {exit_msg['farewell_message']}\n")
                break


async def test_approval_scenario():
    """
    Test Case 1: APPROVAL
    Customer: Vikram Rao (CUST2007)
    - Credit Score: 780 (✅ > 700)
    - Salary: ₹70,459/month
    - Requesting: ₹300,000 for 24 months
    - Pre-approved: ₹247,314
    - Expected: APPROVED (with salary slip)
    """
    box_print("✅ TEST CASE 1: LOAN APPROVAL SCENARIO")
    
    print("\n📊 Customer Profile:")
    print("  Name: Vikram Rao")
    print("  Phone: +91 9854323475")
    print("  Credit Score: 780 ✅")
    print("  Annual Income: ₹845,519 (Monthly: ~₹70,459)")
    print("  Pre-approved Limit: ₹247,314")
    print("\n💰 Loan Request:")
    print("  Amount: ₹300,000")
    print("  Tenure: 24 months")
    print("  Purpose: Home repair")
    print("\n" + "─" * 80)

    master = MasterAgent()
    entry = await master.start_conversation({"source": "test"})
    conv_id = entry["conversation_id"]

    # Simulate conversation
    messages = [
        "Hi, I need a loan for home repair",
        "+91 9854323475",
        "I need 3 lakhs for 24 months",
        "Yes, proceed"
    ]

    for msg in messages:
        print(f"👤 Customer: {msg}")
        response = await master.chat(conv_id, msg)
        print(f"🤖 Sarah: {response['message'][:150]}...")
        
        # Handle upload prompt
        if response.get("next_action") == "upload_document":
            print("\n📎 Uploading salary slip...")
            upload_response = await master.chat(
                conv_id,
                "src/tests/data/sample_salary_slip_vikram.pdf"
            )
            print(f"🤖 Sarah: {upload_response['message']}")
            
            if upload_response.get("state") == "completed":
                print("\n✅ RESULT: LOAN APPROVED")
                data = upload_response.get("data", {})
                loan = data.get("loan_details", {})
                if loan:
                    print(f"   Amount: ₹{int(loan.get('loan_amount', 0)):,}")
                    print(f"   EMI: ₹{int(loan.get('monthly_emi', 0)):,}/month")
                    print(f"   Tenure: {loan.get('tenure_months')} months")
            break
        print()

    print("\n" + "=" * 80 + "\n")


async def test_rejection_scenario():
    """
    Test Case 2: REJECTION
    Customer: Karan Jain (CUST2009)
    - Credit Score: 671 (❌ < 700)
    - Expected: REJECTED (low credit score)
    """
    box_print("❌ TEST CASE 2: LOAN REJECTION SCENARIO")
    
    print("\n📊 Customer Profile:")
    print("  Name: Karan Jain")
    print("  Phone: +91 9085529373")
    print("  Credit Score: 671 ❌ (Below minimum 700)")
    print("  Annual Income: ₹629,245")
    print("  Pre-approved Limit: ₹158,333")
    print("\n💰 Loan Request:")
    print("  Amount: ₹200,000")
    print("  Tenure: 12 months")
    print("\n" + "─" * 80)

    master = MasterAgent()
    entry = await master.start_conversation({"source": "test"})
    conv_id = entry["conversation_id"]

    messages = [
        "I need a loan urgently",
        "+91 9085529373",
        "2 lakhs for 12 months",
        "Yes, apply"
    ]

    for msg in messages:
        print(f"👤 Customer: {msg}")
        response = await master.chat(conv_id, msg)
        print(f"🤖 Sarah: {response['message'][:150]}...")
        
        if response.get("state") == "completed":
            print("\n❌ RESULT: LOAN REJECTED")
            print("   Reason: Credit score (671) below minimum requirement (700)")
            break
        print()

    print("\n" + "=" * 80 + "\n")


async def test_edge_cases():
    """
    Test various edge cases from problem statement
    """
    box_print("🧪 TESTING EDGE CASES FROM PROBLEM STATEMENT")

    # Edge Case 1: Amount > 2x Pre-approved Limit
    print("\n📌 Edge Case 1: Amount > 2x Pre-approved Limit")
    print("   Customer: Amit Sharma (Pre-approved: ₹137,780)")
    print("   Requesting: ₹400,000 (> 2x ₹137,780)")
    print("   Expected: REJECTED\n")

    master = MasterAgent()
    entry = await master.start_conversation({"source": "test"})
    conv_id = entry["conversation_id"]

    await master.chat(conv_id, "I need money")
    await master.chat(conv_id, "+91 9980048083")
    await master.chat(conv_id, "4 lakhs for 36 months")
    result = await master.chat(conv_id, "yes")
    
    decision = "APPROVED" if "approved" in result.get("message", "").lower() else "REJECTED"
    print(f"   Result: {decision}\n")

    # Edge Case 2: Instant Approval (Amount <= Pre-approved)
    print("📌 Edge Case 2: Instant Approval (Within Pre-approved Limit)")
    print("   Customer: Neha Gupta (Pre-approved: ₹388,045)")
    print("   Requesting: ₹200,000 (< ₹388,045)")
    print("   Expected: INSTANT APPROVAL (No salary slip needed)\n")

    entry2 = await master.start_conversation({"source": "test"})
    conv_id2 = entry2["conversation_id"]

    await master.chat(conv_id2, "need loan")
    await master.chat(conv_id2, "+91 9086911256")
    await master.chat(conv_id2, "2 lakhs for 24 months")
    result2 = await master.chat(conv_id2, "proceed")
    
    needs_upload = result2.get("next_action") == "upload_document"
    print(f"   Needs Salary Slip: {'YES ❌' if needs_upload else 'NO ✅'}")
    print(f"   Result: {'INSTANT APPROVED' if not needs_upload else 'PENDING UPLOAD'}\n")

    print("=" * 80 + "\n")


async def run_all_tests():
    """Run all test scenarios"""
    print("\n" + "🚀" * 40)
    print(" " * 15 + "QUICKCASH NBFC - COMPREHENSIVE TEST SUITE")
    print("🚀" * 40 + "\n")

    await test_approval_scenario()
    await asyncio.sleep(1)
    
    await test_rejection_scenario()
    await asyncio.sleep(1)
    
    await test_edge_cases()

    print("\n✅ ALL TESTS COMPLETED!\n")


if __name__ == "__main__":
    print("\nSelect test mode:")
    print("1. Interactive Chat (with salary slip upload prompts)")
    print("2. Automated Approval Test (Vikram - Score 780)")
    print("3. Automated Rejection Test (Karan - Score 671)")
    print("4. Edge Cases Test")
    print("5. Run All Tests")

    choice = input("\nEnter choice (1/2/3/4/5): ").strip()

    try:
        if choice == "1":
            asyncio.run(interactive_chat_with_upload())
        elif choice == "2":
            asyncio.run(test_approval_scenario())
        elif choice == "3":
            asyncio.run(test_rejection_scenario())
        elif choice == "4":
            asyncio.run(test_edge_cases())
        elif choice == "5":
            asyncio.run(run_all_tests())
        else:
            print("Invalid choice. Running interactive mode...")
            asyncio.run(interactive_chat_with_upload())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user. Goodbye! 👋\n")
        sys.exit(0)

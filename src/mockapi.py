# src/mockapi.py
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import os
import json
import asyncio
import logging
import sys
from typing import Dict, Any

# Make sure project src/ is importable when running this file directly
ROOT = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(ROOT)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

# Import database helper (existing file)
try:
    # try original style used in repo
    from data.database import NBFCDatabase, DB_PATH
except Exception:
    # fallback to src.data (if your tests run from different cwd)
    from src.data.database import NBFCDatabase, DB_PATH

# Import VerificationAgent & AgentMessage (used for OCR verification)
# Try both import styles to be resilient to module path differences.
VerificationAgent = None
AgentMessage = None
try:
    from src.agents.verification_agent import VerificationAgent
    from src.agents.base_agent import AgentMessage
except Exception:
    try:
        from agents.verification_agent import VerificationAgent
        from agents.base_agent import AgentMessage
    except Exception:
        VerificationAgent = None
        AgentMessage = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mockapi")

app = FastAPI(title="NBFC Mock API")

# Use DB_PATH from data.database so DB lands in src/nbfc_data.db as expected
db = NBFCDatabase(DB_PATH)

# Allow local requests from Streamlit / other local UIs
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501", "http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # attempt to parse JSON body for logging; ignore non-json silently
    try:
        body = await request.json()
    except Exception:
        body = None
    logger.info(f"[HTTP] {request.method} {request.url.path} body={body}")
    response = await call_next(request)
    logger.info(f"[HTTP] {request.method} {request.url.path} -> {response.status_code}")
    return response

@app.get("/")
async def root():
    return {"message": "NBFC Mock API"}

@app.get("/crm/customer/{customer_id}")
async def get_customer(customer_id: str):
    c = db.get_customer(customer_id)
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    # deterministic: for testing keep docs verified (you can randomize if you want to test partial flow)
    return {
        "customer_id": c["customer_id"],
        "name": c["name"],
        "phone": c["phone"],
        "email": c["email"],
        "city": c["city"],
        "documents": {
            "aadhar": "verified",
            "pan": "verified",
            "address_proof": "verified"
        }
    }

@app.get("/offers/customer/{customer_id}")
async def get_offers(customer_id: str):
    c = db.get_customer(customer_id)
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    limit = int(c["pre_approved_limit"])
    score = int(c["credit_score"])
    if score >= 750:
        rate = 11.0
    elif score >= 700:
        rate = 13.0
    else:
        rate = 18.0
    return {
        "customer_id": customer_id,
        "offers": [
            {"offer_id": f"STD_{customer_id}", "max_amount": limit, "interest_rate": rate, "tenure_months":[12,24,36]}
        ]
    }

@app.get("/credit-bureau/score/{customer_id}")
async def credit_score(customer_id: str):
    c = db.get_customer(customer_id)
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"customer_id": customer_id, "credit_score": int(c["credit_score"])}

@app.post("/upload/salary-slip")
async def upload_salary_slip(customer_id: str = Form(...), file: UploadFile = File(None)):
    # Simulate small processing delay
    await asyncio.sleep(0.5)
    import random
    ok = random.choice([True, True, False])
    if ok:
        monthly = random.randint(25000, 120000)
        # optionally save file if one provided
        if file:
            os.makedirs("uploads", exist_ok=True)
            out_path = os.path.join("uploads", f"salary_{customer_id}_{file.filename}")
            with open(out_path, "wb") as f:
                f.write(await file.read())
        return {"upload_status":"success", "customer_id":customer_id, "validation_result":{"verified":True, "monthly_salary":monthly, "employer":"MockCo"}}
    return {"upload_status":"failed", "customer_id":customer_id, "error":"bad file"}

# -------------------------
# New: KYC document upload + OCR-augmented verification
# -------------------------
@app.post("/upload/kyc-document")
async def upload_kyc_document(customer_id: str = Form(...), file: UploadFile = File(...)):
    """
    Accepts KYC document (Aadhar/PAN/ID proof).
    Saves file to uploads/ and invokes VerificationAgent (if present) with document_path.
    Returns the verification result.
    """
    os.makedirs("uploads", exist_ok=True)
    filename = f"kyc_{customer_id}_{file.filename}"
    save_path = os.path.join("uploads", filename)
    # write file to disk
    try:
        with open(save_path, "wb") as f:
            f.write(await file.read())
    except Exception as e:
        logger.error(f"Failed saving uploaded file: {e}")
        return JSONResponse(status_code=500, content={"error": "failed_to_save_file", "detail": str(e)})

    # If VerificationAgent is available, call it with document_path (agent performs OCR if implemented)
    if VerificationAgent and AgentMessage:
        try:
            agent = VerificationAgent()
            msg = AgentMessage(sender="mockapi", recipient="verification_agent", content={"customer_id": customer_id, "document_path": save_path})
            # handle might be async (as in our implementation), so await it
            result_msg = await agent.handle(msg)
            payload = result_msg.content or {}
            payload.update({"upload_status": "success", "file_saved": save_path})
            logger.info(f"KYC verification for {customer_id}: {payload.get('verification_status')}")
            return payload
        except Exception as e:
            logger.exception(f"VerificationAgent raised an error: {e}")
            return JSONResponse(status_code=500, content={"error": "verification_failed", "detail": str(e)})
    else:
        # Agent not available — return a simulated response so UI can continue
        logger.warning("VerificationAgent not importable; returning simulated verification response.")
        # fallback: simple deterministic response (mirrors /crm/customer)
        c = db.get_customer(customer_id)
        if not c:
            return JSONResponse(status_code=404, content={"error": "customer_not_found"})
        simulated = {
            "customer_id": customer_id,
            "customer_name": c["name"],
            "verification_status": "passed",
            "verified_docs": ["aadhar", "pan", "address_proof"],
            "missing_docs": [],
            "ocr_verification": "not_performed",
            "message": f"Dear {c['name']}, your KYC document was uploaded (saved at {save_path}).",
            "upload_status": "success",
            "file_saved": save_path
        }
        return simulated

if __name__ == "__main__":
    # run the app directly with uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")

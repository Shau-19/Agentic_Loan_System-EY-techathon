# src/tests/test_verification_agent.py
import asyncio
import sys
import os

# ensure repo src/ is importable when running tests from project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.agents.verification_agent import VerificationAgent
from src.agents.base_agent import AgentMessage

async def run_crm_only_test():
    agent = VerificationAgent()
    msg = AgentMessage(sender="tester", content={"customer_id": "CUST2001"})
    resp = await agent.handle(msg)
    print("\n=== CRM-only Verification response ===")
    print(resp.content)

async def run_ocr_test(sample_path: str):
    if not os.path.exists(sample_path):
        print(f"\nSKIP OCR test: sample file not found at {sample_path}")
        print("Place a PDF/image named 'aadhar_sample.pdf' under the uploads/ folder to run OCR test.")
        return

    agent = VerificationAgent()
    msg = AgentMessage(sender="tester", content={"customer_id": "CUST2001", "document_path": sample_path})
    resp = await agent.handle(msg)
    print("\n=== OCR-augmented Verification response ===")
    print(resp.content)

async def main():
    print("Running VerificationAgent tests...")
    await run_crm_only_test()

    # attempt OCR test with sample file placed at repo_root/uploads/aadhar_sample.pdf
    sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads", "aadhar_sample.pdf")
    await run_ocr_test(sample)

if __name__ == "__main__":
    asyncio.run(main())

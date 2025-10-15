# src/tests/test_sanction_agent_no_llm.py
import asyncio
from src.agents.sanction_agent import SanctionAgent
from src.agents.base_agent import AgentMessage

async def main():
    agent = SanctionAgent()
    msg = AgentMessage(sender="tester", content={
        "customer_id": "CUST2005",
        "decision": "approved",
        "loan_details": {
            "loan_amount": 500000,
            "tenure_months": 36,
            "interest_rate": 12.5,
            "monthly_emi": None,
            "application_id": "LOAN20251007123456",
            "processing_fee": 10000
        },
    })
    resp = await agent.handle(msg)
    print("Resp:", resp.content)
    print("PDF exists:", resp.content.get("pdf_path") and __import__("os").path.exists(resp.content.get("pdf_path")))

if __name__ == "__main__":
    asyncio.run(main())

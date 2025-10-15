# src/tests/test_underwriting_agent.py
import asyncio
from src.agents.underwriting_agent import UnderwritingAgent
from src.agents.base_agent import AgentMessage
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    agent = UnderwritingAgent()
    # CUST2007 with loan amount large enough to require salary slip
    msg = AgentMessage(sender="tester", content={"customer_id": "CUST2002", "loan_amount": 500000, "tenure_months": 36})
    resp = await agent.handle(msg)
    print("Underwriting response:", resp.content)

if __name__ == "__main__":
    asyncio.run(main())

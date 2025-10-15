# src/tests/test_sales_agent.py
import asyncio
from src.agents.sales_agent import SalesAgent
from src.agents.base_agent import AgentMessage
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    agent = SalesAgent()
    msg = AgentMessage(sender="tester", content={"customer_id": None, "user_input": "I want a personal loan for 3 lakhs over 2 years"})
    resp = await agent.handle(msg)
    print("Sales response:", resp.content)

if __name__ == "__main__":
    asyncio.run(main())

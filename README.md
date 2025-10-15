# 🏦 Agentic Loan System - EY Techathon

<div align="center">

![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![LangChain](https://img.shields.io/badge/LangChain-0.1.16-green.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

**An intelligent multi-agent loan processing system powered by LangChain, LangGraph, and Groq LLM**

[Features](#-features) • [Architecture](#-architecture) • [Setup](#-quick-start) • [Usage](#-usage) • [Testing](#-testing)

</div>

---

## 🎯 Overview

An **AI-powered loan processing system** that automates the entire loan approval workflow using a multi-agent architecture. The system handles everything from initial customer inquiry to final sanction letter generation, including OCR-based document verification and intelligent anomaly detection.

### Key Highlights

- **Automated Workflow**: End-to-end loan processing with minimal human intervention
- **Intelligent OCR**: Extracts salary information from uploaded documents
- **Manual Review System**: Automatically flags applications with anomalies
- **Real-time Processing**: Instant credit checks and eligibility verification
- **PDF Generation**: Professional sanction letters generated on approval

---

## ✨ Features

### 🤖 Multi-Agent System
- **Sales Agent**: Customer onboarding and loan requirement gathering
- **Verification Agent**: Identity and phone verification
- **Underwriting Agent**: Credit assessment, OCR processing, and risk evaluation
- **Sanction Agent**: Approval letter generation and PDF creation
- **Master Agent**: Conversation orchestration and user interaction
- **Orchestrator**: Pipeline state management and agent coordination

### 📄 OCR Document Processing
- Automatic salary slip text extraction using Tesseract
- Intelligent salary detection with multiple heuristics
- Confidence scoring for extracted information
- Support for PDF documents

### 🔍 Intelligent Decision Making
- Credit score-based eligibility checks
- EMI-to-salary ratio validation
- Pre-approved limit verification
- Automatic anomaly detection

### ⚠️ Manual Review System
- Automatic flagging of suspicious applications
- Detailed anomaly reports in JSON format
- Human-in-the-loop workflow for edge cases

---

## 🏗️ System Architecture

<div align="center">
                       
                       ┌─────────────────────┐
                       │   User Interface    │
                       │ (Chat / REST API)   │
                       └──────────┬──────────┘
                       │
                       ▼
                       ┌─────────────────────┐
                       │   Master Agent      │
                       │  Conversation &     │
                       │  State Manager      │
                       └──────────┬──────────┘
                       │
                       ▼
                       ┌─────────────────────┐
                       │  Orchestrator       │
                       │  (LangGraph)        │
                       │  Pipeline Router    │
                       └──────────┬──────────┘
                       │
       ┌──────────────┬───────────┼───────────┬──────────────┐
       │              │           │           │              │
       ▼              ▼           ▼           ▼              ▼
    ┌──────────┐  ┌──────────┐ ┌──────────┐ ┌──────────┐  ┌──────────┐
    │  Sales   │  │  Verify  │ │Underwrite│ │ Sanction │  │  (More)  │
    │  Agent   │  │  Agent   │ │  Agent   │ │  Agent   │  │  Agents  │
    └─────┬────┘  └─────┬────┘ └─────┬────┘ └─────┬────┘  └──────────┘
          │             │            │            │
          └─────────────┴────────────┴────────────┘
                                │
                                ▼
                  ┌─────────────────────────────┐
                  │  Integration Layer          │
                  ├─────────────────────────────┤
                  │ -  Mock Credit Bureau API    │
                  │ -  SQLite Database           │
                  │ -  Tesseract OCR Engine      │
                  │ -  ReportLab PDF Generator   │
                  └─────────────────────────────┘
</div>

## 🛠️ Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Framework** | LangChain 0.1.16 | Agent framework and LLM orchestration |
| **Orchestration** | LangGraph 0.2.16 | State management and workflow routing |
| **LLM** | Groq (llama-3.3-70b-versatile) | Natural language understanding and generation |
| **OCR** | Tesseract + PyMuPDF | Document text extraction |
| **PDF Generation** | ReportLab | Sanction letter creation |
| **Database** | SQLite3 | Customer and application data storage |
| **API** | FastAPI + Uvicorn | Mock credit bureau service |
| **Language** | Python 3.9+ | Core application development |

---

## 📋 Prerequisites

### Required

- **Python 3.9+** - [Download](https://www.python.org/downloads/)
- **Groq API Key** - [Get Free Key](https://console.groq.com/keys)
- **Tesseract OCR** - Installation instructions below

  ### Installing Tesseract OCR

  **Windows:**
    Download from: **https://github.com/UB-Mannheim/tesseract/wiki**
    Or use Chocolatey:
    choco install tesseract

  **Linux (Ubuntu/Debian):**
    sudo apt-get update
    sudo apt-get install tesseract-ocr tesseract-ocr-eng
  **macOS:**
    brew install tesseract


  **Verify Installation:**
    tesseract --version

---

## 🚀 Quick Start

### 1. Clone the Repository
  -git clone https://github.com/Shau-19/Agentic_Loan_System-EY-techathon.git
  
  -cd Agentic_Loan_System-EY-techathon

### 2. Create Virtual Environment

  -Create virtual environment
    python -m venv venv

  -Activate (Windows)
    venv\Scripts\activate

  -Activate (Linux/Mac)
    source venv/bin/activate

### 3. Install Dependencies
  pip install -r requirements.txt


### 4. Configure Environment
  -Copy template
    cp src/.env.example src/.env

  -Edit and add your Groq API key
    notepad src/.env # Windows
    nano src/.env # Linux/Mac

  
**Add to `src/.env`:**
  GROQ_API_KEY=gsk_your_actual_groq_api_key_here
  
  GROQ_MODEL=llama-3.3-70b-versatile

  
### 5. Initialize Database

  python -m src.data.database


### 6. Start Mock API (Terminal 1)

  python -m uvicorn src.mockapi:app --reload --port 8001


### 7. Run Application (Terminal 2)

  streamlit run demo.py

---

## 📁 Project Structure

Agentic_Loan_System-EY-techathon/
│
├── src/
│ ├── .env.example # Environment template
│ ├── agents/ # Agent implementations
│ │ ├── master_agent.py # Conversation handler
│ │ ├── orchestrator.py # Pipeline orchestrator
│ │ ├── sales_agent.py # Sales qualification
│ │ ├── verification_agent.py
│ │ ├── underwriting_agent.py
│ │ └── sanction_agent.py
│ ├── data/
│ │ └── database.py # SQLite manager
│ ├── utils/
│ │ └── ocr_utils.py # OCR helpers
│ ├── tests/ # Test suite
│ └── mockapi.py # Mock credit API
│
├── generated_documents/ # Output PDFs
├── manual_reviews/ # Manual review snapshots
├── nbfc_loan_system.db # SQLite database
├── requirements.txt
└── README.md


---

## 💾 Database Setup

### Sample Data

| Name | Phone | Credit Score | Status |
|------|-------|--------------|--------|
| Vikram Rao | +91 9854323475 | 780 | Approved |
| Neha Gupta | +91 9086911256 | 740 | Approved |
| Karan Jain | +91 9085529373 | 671 | Rejected |

### Verify Database
  python -c "from src.data.database import NBFCDatabase; db = NBFCDatabase(); print(f'✅ {len(db.get_all_customers())} customers loaded')"

---

## 📊 Performance

- **Processing Time**: ~5-8 seconds/application
- **OCR Processing**: ~2-3 seconds/document
- **LLM Response**: ~1-2 seconds/call
- **Database Ops**: <100ms

---

## 🔐 Security

- ✅ API keys in `.env` (not in Git)
- ✅ Database excluded from version control
- ✅ No hardcoded credentials
- ⚠️ Demo system - not production-ready

---

## 🚀 Future Enhancements

- [ ] Web UI with Streamlit
- [ ] Real credit bureau integration
- [ ] Multi-language support
- [ ] Advanced fraud detection
- [ ] Email/SMS notifications
- [ ] Admin dashboard
- [ ] Excel export

---

  






### How It Works

1. **User Interaction**: Customer initiates loan request through interactive chat
2. **Conversation Management**: Master Agent interprets intent and maintains context
3. **Workflow Orchestration**: LangGraph routes request through appropriate agents
4. **Sequential Processing**: Each agent performs its specialized task and passes results forward
5. **Decision Making**: Underwriting agent makes approval/rejection decision or flags for review
6. **Document Generation**: Approved loans trigger automatic PDF generation
7. **Response Delivery**: Final decision communicated back to user with all relevant details

---

⚠️ **Note**: This is a demonstration system for the EY Techathon. Production deployment would require:
- Real credit bureau integration
- Enhanced security (OAuth, JWT)
- Regulatory compliance (KYC, AML)
- Rate limiting and DDoS protection

---
## 📊 Project Stats

- **Number of Agents**: 4 specialist agents + 1 master + 1 orchestrator
- **Processing Time**: ~5-8 seconds per application
- **Test Coverage**: Comprehensive test suite for all agents

---




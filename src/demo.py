import os
import sys
import time
import asyncio
from datetime import datetime
import streamlit as st


ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT = os.path.dirname(ROOT)
if PROJ_ROOT not in sys.path:
    sys.path.insert(0, PROJ_ROOT)

from src.data.database import NBFCDatabase
from src.agents.master_agent import MasterAgent


st.set_page_config(
    page_title="QuickCash AI Loan Assistant",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    /* Dark theme */
    :root {
        --bg: #0a0e27;
        --card: #141b3d;
        --accent: #00d4ff;
        --bubble-user: #1a4d2e;
        --bubble-bot: #1a2332;
        --text: #e8eef5;
        --muted: #8b95a8;
    }
    
    .stApp {
        background: linear-gradient(135deg, #0a0e27 0%, #141b3d 100%);
        color: var(--text);
    }
    
    /* Main title */
    .main-title {
        text-align: center;
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(90deg, #00d4ff, #00ff88);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 5px 0 8px 0;
        text-shadow: 0 0 30px rgba(0, 212, 255, 0.3);
    }
    
    .subtitle {
        text-align: center;
        color: var(--muted);
        font-size: 1.1rem;
        margin-bottom: 8px;
    }
    
    /* Chat area */
    .chat-container {
        background: rgba(20, 27, 61, 0.4);
        border: 1px solid rgba(0, 212, 255, 0.1);
        border-radius: 15px;
        padding: 20px;
        min-height: 52vh;
        max-height: 62vh;
        overflow-y: auto;
        backdrop-filter: blur(10px);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        margin-top: 5px;
    }
    
    /* Chat bubbles */
    .chat-bubble-user {
        background: linear-gradient(135deg, #1a4d2e, #2d7a4f);
        color: #ffffff;
        padding: 12px 16px;
        border-radius: 18px 18px 4px 18px;
        margin: 10px 0;
        max-width: 70%;
        float: right;
        clear: both;
        word-wrap: break-word;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    }
    
    .chat-bubble-bot {
        background: linear-gradient(135deg, #1a2332, #2d3548);
        color: var(--text);
        padding: 12px 16px;
        border-radius: 18px 18px 18px 4px;
        margin: 10px 0;
        max-width: 75%;
        float: left;
        clear: both;
        word-wrap: break-word;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        border-left: 3px solid var(--accent);
    }
    
    .timestamp {
        font-size: 0.7rem;
        color: var(--muted);
        margin-top: 4px;
        opacity: 0.7;
    }
    
    .typing-indicator {
        color: var(--accent);
        font-style: italic;
        font-size: 0.9rem;
        margin: 5px 0;
    }
    
    /* Loan summary card */
    .loan-card {
        background: linear-gradient(135deg, rgba(0, 212, 255, 0.05), rgba(0, 255, 136, 0.05));
        border: 1px solid rgba(0, 212, 255, 0.2);
        border-radius: 12px;
        padding: 16px;
        margin: 15px 0;
        color: var(--text);
        box-shadow: 0 4px 16px rgba(0, 212, 255, 0.1);
    }
    
    .loan-card h4 {
        color: var(--accent);
        margin-top: 0;
        font-size: 1.1rem;
    }
    
    .loan-card p {
        margin: 8px 0;
        font-size: 0.95rem;
    }
    
    /* Timeline */
    .timeline {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin: 20px 0;
        padding: 10px;
    }
    
    .step {
        display: flex;
        flex-direction: column;
        align-items: center;
        flex: 1;
        text-align: center;
    }
    
    .dot {
        width: 20px;
        height: 20px;
        border-radius: 50%;
        margin-bottom: 8px;
        transition: all 0.3s ease;
    }
    
    .dot.pending {
        background: rgba(139, 149, 168, 0.3);
        border: 2px solid rgba(139, 149, 168, 0.5);
    }
    
    .dot.active {
        background: linear-gradient(135deg, var(--accent), #00ff88);
        box-shadow: 0 0 20px rgba(0, 212, 255, 0.6);
        border: 2px solid var(--accent);
    }
    
    .step-label {
        font-size: 0.85rem;
        font-weight: 600;
        margin-top: 5px;
    }
    
    .step-label.pending {
        color: var(--muted);
    }
    
    .step-label.active {
        color: var(--accent);
    }
    
    .connector {
        height: 3px;
        flex: 1;
        background: rgba(139, 149, 168, 0.3);
        margin: 0 10px;
    }
    
    .connector.active {
        background: linear-gradient(90deg, var(--accent), #00ff88);
        box-shadow: 0 0 10px rgba(0, 212, 255, 0.5);
    }
    
    /* Input styling */
    .stTextInput > div > div > input {
        background: rgba(20, 27, 61, 0.6) !important;
        color: var(--text) !important;
        border: 1px solid rgba(0, 212, 255, 0.3) !important;
        border-radius: 10px !important;
        padding: 12px !important;
    }
    
    .stTextInput > div > div > input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 15px rgba(0, 212, 255, 0.3) !important;
    }
    
    /* Button styling */
    .stButton > button {
        background: linear-gradient(135deg, var(--accent), #00ff88) !important;
        color: #0a0e27 !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 10px 24px !important;
        font-weight: 700 !important;
        transition: all 0.3s ease !important;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 20px rgba(0, 212, 255, 0.4) !important;
    }
    
    /* File uploader */
    .stFileUploader {
        background: rgba(20, 27, 61, 0.6);
        border: 2px dashed rgba(0, 212, 255, 0.3);
        border-radius: 10px;
        padding: 10px;
    }
    
    /* Sidebar */
    .css-1d391kg, [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0a0e27 0%, #141b3d 100%);
    }
    
    /* Metrics */
    .metric-card {
        background: rgba(0, 212, 255, 0.05);
        border: 1px solid rgba(0, 212, 255, 0.2);
        border-radius: 10px;
        padding: 15px;
        text-align: center;
        margin: 10px 0;
    }
    
    .metric-value {
        font-size: 1.8rem;
        font-weight: 800;
        color: var(--accent);
    }
    
    .metric-label {
        font-size: 0.9rem;
        color: var(--muted);
        margin-top: 5px;
    }
</style>
""", unsafe_allow_html=True)


def run_async(coro):
    """Safely run async coroutine"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro)
        loop.close()
        return result
    except Exception as e:
        st.error(f"Error running async function: {e}")
        return None


@st.cache_resource
def init_backend():
    """Initialize database and master agent (cached)"""
    db = NBFCDatabase()
    master = MasterAgent()
    return db, master

db, master = init_backend()


if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'conversation_id' not in st.session_state:
    st.session_state.conversation_id = None
if 'conversation_started' not in st.session_state:
    st.session_state.conversation_started = False
if 'awaiting_salary' not in st.session_state:
    st.session_state.awaiting_salary = False
if 'stage_status' not in st.session_state:
    st.session_state.stage_status = {
        "Sales": False,
        "Verification": False,
        "Underwriting": False,
        "Sanction": False
    }
if 'loan_details' not in st.session_state:
    st.session_state.loan_details = {}
if 'sanction_letter_path' not in st.session_state:
    st.session_state.sanction_letter_path = None
if 'input_key' not in st.session_state:
    st.session_state.input_key = 0


def add_message(role: str, text: str):
    """Add message to chat history"""
    st.session_state.chat_history.append({
        "role": role,
        "text": text,
        "time": datetime.now().strftime("%H:%M:%S")
    })

def typing_effect(container, text: str, delay: float = 0.01):
    """Simulate typing animation"""
    placeholder = container.empty()
    status_placeholder = container.empty()
    
    # Show typing indicator
    status_placeholder.markdown(
        "<div class='typing-indicator'>Sarah is typing...</div>",
        unsafe_allow_html=True
    )
    
    displayed_text = ""
    for char in text:
        displayed_text += char
        placeholder.markdown(
            f"<div class='chat-bubble-bot'>{displayed_text}</div>",
            unsafe_allow_html=True
        )
        time.sleep(delay)
    
    
    status_placeholder.empty()
    
    add_message("assistant", text)

def update_stage_status(conv_state):
    """Update pipeline stage status"""
    if not conv_state:
        return
    
    flow = conv_state.get('flow')
    if flow:
        st.session_state.stage_status = {
            "Sales": bool(flow.sales_result),
            "Verification": bool(flow.verification_result),
            "Underwriting": bool(flow.underwriting_result),
            "Sanction": bool(flow.sanction_result)
        }
        
        # Extract loan details
        if flow.underwriting_result:
            uw_result = flow.underwriting_result
            st.session_state.loan_details = uw_result.get('loan_details', {})
            
            # Check for sanction letter
            if flow.sanction_result:
                st.session_state.sanction_letter_path = flow.sanction_result.get('pdf_path')

def render_timeline():
    """Render progress timeline"""
    steps = ["Sales", "Verification", "Underwriting", "Sanction"]
    status = st.session_state.stage_status
    
    html = "<div class='timeline'>"
    
    for i, step in enumerate(steps):
        is_active = status.get(step, False)
        dot_class = "dot active" if is_active else "dot pending"
        label_class = "step-label active" if is_active else "step-label pending"
        
        html += f"""
        <div class='step'>
            <div class='{dot_class}'></div>
            <div class='{label_class}'>{step}</div>
        </div>
        """
        
        
        if i < len(steps) - 1:
            next_active = status.get(steps[i + 1], False)
            conn_class = "connector active" if is_active and next_active else "connector"
            html += f"<div class='{conn_class}'></div>"
    
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

def get_customer_count():
    """Get total number of customers from database"""
    try:
        cur = db.conn.execute("SELECT COUNT(*) as count FROM customers")
        result = cur.fetchone()
        return result['count'] if result else 0
    except:
        return 10  


with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/bank.png", width=80)
    
    st.markdown("### 🏦 QuickCash NBFC")
    st.markdown("**AI-Powered Loan Assistant**")
    
    st.markdown("---")
    
    st.markdown("### 💡 Features")
    st.markdown("""
    - ✅ Instant eligibility check
    - ✅ Real-time loan approval
    - ✅ OCR-based verification
    - ✅ Automated decision-making
    - ✅ Digital sanction letter
    """)
    
    st.markdown("---")
    
    st.markdown("### 📊 Loan Options")
    st.markdown("""
    - **Amount:** ₹10K - ₹5L
    - **Tenure:** 12, 24, 36 months
    - **Interest:** From 10.5% p.a.
    - **Decision:** Under 2 minutes
    """)
    
    st.markdown("---")
    
    # Quick stats
    customer_count = get_customer_count()
    
    st.markdown('<div class="metric-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="metric-value">{customer_count}</div>', unsafe_allow_html=True)
    st.markdown('<div class="metric-label">Active Customers</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown('<div class="metric-card">', unsafe_allow_html=True)
    st.markdown('<div class="metric-value">78%</div>', unsafe_allow_html=True)
    st.markdown('<div class="metric-label">Approval Rate</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Demo credentials
    with st.expander("🔑 Demo Credentials"):
        st.markdown("""
        **Approved:**
        - +91 9854323475 (Vikram)
        - +91 9086911256 (Neha)
        
        **Rejected:**
        - +91 9085529373 (Karan - Low score)
        """)
    
    st.markdown("---")
    
    if st.button("🔄 Reset Chat", use_container_width=True):
        st.session_state.chat_history = []
        st.session_state.conversation_started = False
        st.session_state.conversation_id = None
        st.session_state.awaiting_salary = False
        st.session_state.loan_details = {}
        st.session_state.sanction_letter_path = None
        st.session_state.input_key += 1
        st.rerun()

col_chat, col_summary = st.columns([2.5, 1])

with col_chat:
    
    st.markdown('<div class="main-title">🏦 QuickCash AI Loan Assistant</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Get instant loan approval in minutes</div>', unsafe_allow_html=True)
    
    
    chat_container = st.container()
    
    with chat_container:
        
        if st.session_state.chat_history:
            st.markdown("<div class='chat-container'>", unsafe_allow_html=True)
            
            
            for msg in st.session_state.chat_history:
                bubble_class = "chat-bubble-user" if msg["role"] == "user" else "chat-bubble-bot"
                st.markdown(
                    f"<div class='{bubble_class}'>{msg['text']}"
                    f"<div class='timestamp'>{msg['time']}</div></div>",
                    unsafe_allow_html=True
                )
            
            st.markdown("</div>", unsafe_allow_html=True)
    
    
    st.markdown("---")
    
    input_col, upload_col, send_col = st.columns([6, 1.5, 1])
    
    with input_col:
        user_input = st.text_input(
            "Message",
            placeholder="Type your message here (e.g., 'I need ₹2 lakhs for 24 months')...",
            key=f"user_input_{st.session_state.input_key}",
            label_visibility="collapsed"
        )
    
    with upload_col:
        if st.session_state.awaiting_salary:
            uploaded_file = st.file_uploader(
                "Upload",
                type=["pdf", "png", "jpg", "jpeg"],
                key=f"file_upload_{st.session_state.input_key}",
                label_visibility="collapsed"
            )
        else:
            uploaded_file = None
    
    with send_col:
        send_button = st.button("📤 Send", use_container_width=True)

with col_summary:
    st.markdown("### 📋 Application Status")
    
   
    render_timeline()
    
    st.markdown("---")
    
    if st.session_state.loan_details:
        details = st.session_state.loan_details
        
        st.markdown(f"""
        <div class='loan-card'>
            <h4>💰 Loan Details</h4>
            <p><strong>Application ID:</strong><br>{details.get('application_id', 'N/A')}</p>
            <p><strong>Amount:</strong><br>₹{int(details.get('loan_amount', 0)):,}</p>
            <p><strong>Tenure:</strong><br>{details.get('tenure_months', 0)} months</p>
            <p><strong>Monthly EMI:</strong><br>₹{int(details.get('monthly_emi', 0)):,}</p>
            <p><strong>Interest Rate:</strong><br>{details.get('interest_rate', 0)}% p.a.</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Download button
        if st.session_state.sanction_letter_path and os.path.exists(st.session_state.sanction_letter_path):
            with open(st.session_state.sanction_letter_path, "rb") as pdf_file:
                pdf_bytes = pdf_file.read()
                st.download_button(
                    label="📥 Download Sanction Letter",
                    data=pdf_bytes,
                    file_name="sanction_letter.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
    else:
        st.markdown("""
        ### 💡 Quick Tips
        - Share your phone number
        - Mention loan amount
        - Choose tenure (12/24/36 months)
        - Upload salary slip if needed
        """)


if not st.session_state.conversation_started:
    
    result = run_async(master.start_conversation({"source": "website"}))
    if result:
        st.session_state.conversation_id = result['conversation_id']
        st.session_state.conversation_started = True
        add_message("assistant", result['message'])
        st.rerun()


if send_button and user_input.strip():
    
    add_message("user", user_input)
    
    
    typing_container = chat_container.container()
    
    
    response = run_async(master.chat(st.session_state.conversation_id, user_input))
    
    if response:
        
        typing_effect(typing_container, response.get('message', ''))
        
        
        conv_state = master.get_conversation_state(st.session_state.conversation_id)
        update_stage_status(conv_state)
        
        
        if response.get('next_action') == 'upload_document':
            st.session_state.awaiting_salary = True
        
        
        st.session_state.input_key += 1
        st.rerun()


if uploaded_file is not None:
    
    upload_dir = os.path.join(os.getcwd(), "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = os.path.join(upload_dir, f"{int(time.time())}_{uploaded_file.name}")
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    
    
    add_message("user", f"📎 Uploaded: {uploaded_file.name}")
    
    
    typing_container = chat_container.container()
    
    
    response = run_async(master.chat(st.session_state.conversation_id, file_path))
    
    if response:
        
        typing_effect(typing_container, response.get('message', ''))
        
        
        conv_state = master.get_conversation_state(st.session_state.conversation_id)
        update_stage_status(conv_state)
        
        st.session_state.awaiting_salary = False
        st.session_state.input_key += 1
        st.rerun()


st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #8b95a8; padding: 20px;'>
    <p><strong>QuickCash NBFC</strong> - Powered by Agentic AI</p>
    <p style='font-size: 0.9rem;'>Built with LangChain, Groq LLM, and Streamlit</p>
    <p style='font-size: 0.8rem;'>© 2025 QuickCash. All rights reserved.</p>
</div>
""", unsafe_allow_html=True)
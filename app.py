import streamlit as st
import pandas as pd
import os
import tempfile
from dotenv import load_dotenv
import anthropic
from pypdf2 import PdfReader  # ✅ Lowercase to avoid ModuleNotFoundError

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

st.set_page_config(page_title="RouteVerify Lite - DSNY Demo", layout="wide")
st.title("📋 RouteVerify Lite - DSNY Demo")

# Optional PIN gate
pin = st.text_input("Enter access PIN:", type="password")
if pin != "dsny2025":
    st.warning("🔒 Enter valid PIN to continue.")
    st.stop()

st.header("📄 Upload DS-659 Route Sheet (PDF, JPG, PNG)")
route_file = st.file_uploader("Upload Route Sheet", type=["pdf", "jpg", "jpeg", "png"])

st.header("📍 Upload Rastrac GPS Trail (CSV)")
gps_file = st.file_uploader("Upload Rastrac GPS File", type=["csv"])

claude_json = {}

def extract_text_from_pdf(file_path):
    reader = PdfReader(file_path)
    all_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            all_text += text
    return all_text

def call_claude_text_ocr(file_path):
    route_text = extract_text_from_pdf(file_path)

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": f"""
This is a DS-659 route sheet in raw text. 
Extract the section, route number, truck number, and all ITSA numbers. Return JSON only like this:

{{
  "section": "___",
  "route": "___",
  "truck_number": "___",
  "itsas": ["___", ...]
}}

Text:
{route_text}
"""
            }
        ]
    )

    for block in msg.content:
        if block.type == "text":
            return block.text.strip()

if route_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=route_file.name[-4:]) as tmp:
        tmp.write(route_file.getvalue())
        tmp_path = tmp.name

    st.info("⏳ Running Claude OCR...")
    try:
        raw_json = call_claude_text_ocr(tmp_path)
        st.success("✅ Claude returned JSON:")
        st.code(raw_json, language="json")
        claude_json = eval(raw_json)
    except Exception as e:
        st.error(f"Claude failed: {e}")

if claude_json:
    st.subheader("🧪 SmartScan+ Result (Simulated)")
    data = []
    for i, itsa in enumerate(claude_json["itsas"]):
        data.append({
            "ITSA": itsa,
            "Status": "✅ Verified" if i % 2 == 0 else "❌ Missed",
            "Notes": "" if i % 2 == 0 else "No GPS coverage"
        })
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download Result CSV", data=csv, file_name="smartscan_output.csv")

st.markdown("---")
st.caption("Built for NYC DSNY Supervisors · RouteVerify Lite v1.0 · Claude OCR (Text Mode)")

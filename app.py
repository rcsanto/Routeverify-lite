import streamlit as st
import pandas as pd
import os
import tempfile
from dotenv import load_dotenv
import anthropic
from pypdf import PdfReader
from pdf2image import convert_from_path
import pytesseract
import json

# Load Claude key from environment
load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

# Streamlit setup
st.set_page_config(page_title="RouteVerify Lite - DSNY Demo", layout="wide")
st.title("📋 RouteVerify Lite - DSNY Demo")

# Access PIN
pin = st.text_input("Enter access PIN:", type="password")
if pin != "dsny2025":
    st.warning("🔒 Enter valid PIN to continue.")
    st.stop()

# Upload widgets
st.header("📄 Upload DS-659 Route Sheet (PDF, JPG, PNG)")
route_file = st.file_uploader("Upload Route Sheet", type=["pdf", "jpg", "jpeg", "png"])

st.header("📍 Upload Rastrac GPS Trail (CSV)")
gps_file = st.file_uploader("Upload Rastrac GPS File", type=["csv"])

claude_json = {}

# Extract text from searchable PDF
def extract_text_from_pdf(file_path):
    reader = PdfReader(file_path)
    all_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            all_text += text
    return all_text.strip()

# OCR fallback for scanned PDF
def extract_text_with_ocr(file_path):
    images = convert_from_path(file_path)
    text = ""
    for image in images:
        text += pytesseract.image_to_string(image)
    return text.strip()

# Claude OCR logic
if route_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=route_file.name[-4:]) as tmp:
        tmp.write(route_file.getvalue())
        tmp_path = tmp.name

    st.info("⏳ Running Claude OCR...")

    try:
        route_text = extract_text_from_pdf(tmp_path)
        if not route_text.strip():
            st.warning("⚠️ PDF appears blank. Trying OCR fallb

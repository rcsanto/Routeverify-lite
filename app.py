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
    return all_text.strip()

def extract_text_with_ocr(file_path):
    images = convert_from_path(file_path)
    text = ""
    for image in images:
        text += pytesseract.image_to_string(image)
    return text.strip()

if route_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=route_file.name[-4:]) as tmp:
        tmp.write(route_file.getvalue())
        tmp_path = tmp.name

    st.info("⏳ Running Claude OCR...")

    try:
        route_text = extract_text_from_pdf(tmp_path)
        if not route_text.strip():
            st.warning("⚠️ PDF appears blank. Trying OCR fallback...")
            route_text = extract_text_with_ocr(tmp_path)

        if not route_text.strip():
            st.error("❌ No readable text found — even with OCR fallback.")
            st.stop()

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
  "truck_number":_

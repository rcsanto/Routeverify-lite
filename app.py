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

# Access PIN
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
    images = convert_from_path(f

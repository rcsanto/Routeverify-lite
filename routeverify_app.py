import streamlit as st
import pandas as pd
import openai
import pytesseract
from PIL import Image
import fitz  # PyMuPDF
from pdf2image import convert_from_bytes

def extract_text_from_file(file):
    # If it's a PDF
    if file.name.endswith(".pdf"):
        images = convert_from_bytes(file.read())
        text = ""
        for image in images:
            text += pytesseract.image_to_string(image)
        return text
    
    # If it's an image
    elif file.name.lower().endswith((".jpg", ".jpeg", ".png")):
        image = Image.open(file)
        return pytesseract.image_to_string(image)
    
    return ""

openai.api_key = st.secrets["OPENAI_API_KEY"]

st.title("🛻 RouteVerify Lite - DSNY Demo")

route_file = st.file_uploaderroute_file = st.file_uploader("Upload DS659 Route Sheet (PDF, JPG, PNG)", type=["pdf", "jpg", "jpeg", "png"])

gps_file = st.file_uploader("Upload Rastrac GPS Trail (CSV)", type=["csv"])

if route_file and gps_file:
    route_df = pd.read_csv(route_file)
    gps_df = pd.read_csv(gps_file)

    st.subheader("Parsed Route Sheet")
    st.dataframe(route_df)

    st.subheader("Parsed GPS Trail")
    st.dataframe(gps_df)

    route_text = route_df.to_string(index=False)
    gps_text = gps_df.to_string(index=False)
    prompt = f"""
You are verifying sanitation routes. Compare the ITSA route sheet to GPS stops.

ROUTE SHEET:
{route_text}

GPS:
{gps_text}

Label each ITSA as:
✅ Complete — GPS matches full FROM/TO
❌ Missed — No match
⚠️ Recheck — Partial match
"""

    if st.button("✅ Check Route"):
        with st.spinner("Asking GPT..."):
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a sanitation route analyst."},
                    {"role": "user", "content": prompt}
                ]
            )
            st.markdown("### Result")
            st.markdown(response.choices[0].message.content)

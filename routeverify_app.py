import streamlit as st
from pdf2image import convert_from_bytes
from PIL import Image
import pytesseract
import pandas as pd

st.title("RouteVerify Lite - DSNY Demo")

# Upload Route Sheet
st.subheader("Upload DS659 Route Sheet (PDF, JPG, PNG)")
route_file = st.file_uploader("Upload Route Sheet", type=["pdf", "jpg", "jpeg", "png"])

# Upload GPS Trail
st.subheader("Upload Rastrac GPS Trail (CSV)")
gps_file = st.file_uploader("Upload Rastrac GPS File", type=["csv"])


def extract_text_from_file(file):
    if file.name.lower().endswith(".pdf"):
        images = convert_from_bytes(file.read())
        text = ""
        for image in images:
            text += pytesseract.image_to_string(image)
        return text

    elif file.name.lower().endswith((".jpg", ".jpeg", ".png")):
        image = Image.open(file)
        return pytesseract.image_to_string(image)

    else:
        return ""


def process_gps_file(file):
    df = pd.read_csv(file)
    return df.head()  # Show preview for now


# Processing
if route_file is not None:
    st.subheader("📝 Extracted Route Sheet Text")
    try:
        route_text = extract_text_from_file(route_file)
        st.text_area("Extracted Text", route_text, height=300)
    except Exception as e:
        st.error(f"Failed to process route sheet: {e}")

if gps_file is not None:
    st.subheader("📍 GPS File Preview")
    try:
        gps_preview = process_gps_file(gps_file)
        st.dataframe(gps_preview)
    except Exception as e:
        st.error(f"Failed to process GPS file: {e}")

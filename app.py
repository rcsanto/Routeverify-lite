import streamlit as st
from PIL import Image
from pdf2image import convert_from_bytes
import pytesseract
import pandas as pd
import io

# Streamlit page setup
st.set_page_config(page_title="RouteVerify Lite", layout="centered")
st.title("RouteVerify Lite - DSNY Demo")

# Upload DS-659 route sheet
st.header("Upload DS-659 Route Sheet (PDF, JPG, PNG)")
route_file = st.file_uploader("Upload Route Sheet", type=["pdf", "jpg", "jpeg", "png"])

# Upload GPS trail file
st.header("Upload Rastrac GPS Trail (CSV)")
gps_file = st.file_uploader("Upload Rastrac GPS File", type=["csv"])

# Text extraction helper
def extract_text_from_file(file):
    if file.name.lower().endswith(".pdf"):
        try:
            images = convert_from_bytes(file.read())
            text = ""
            for image in images:
                text += pytesseract.image_to_string(image)
            return text
        except Exception as e:
            return f"Failed to process route sheet: {str(e)}"

    elif file.name.lower().endswith((".jpg", ".jpeg", ".png")):
        try:
            image = Image.open(file)
            return pytesseract.image_to_string(image)
        except Exception as e:
            return f"Failed to process image: {str(e)}"

    return "Unsupported file type."

# Display extracted route text
if route_file:
    with st.expander("📄 Extracted Route Sheet Text"):
        extracted_text = extract_text_from_file(route_file)
        if "Failed" in extracted_text:
            st.error(extracted_text)
        else:
            st.text_area("Extracted Text", extracted_text, height=300)

# Display GPS data table
if gps_file:
    try:
        gps_df = pd.read_csv(gps_file)
        st.success("✅ GPS File Uploaded Successfully")
        st.dataframe(gps_df.head())
    except Exception as e:
        st.error(f"Failed to process GPS CSV: {str(e)}")

# Entry point for Render to run the app
if __name__ == "__main__":
    import streamlit.web.cli as stcli
    import sys
    sys.argv = ["streamlit", "run", "app.py", "--server.port=10000", "--server.address=0.0.0.0"]
    sys.exit(stcli.main())

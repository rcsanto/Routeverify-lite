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
import logging
from typing import Dict, List, Optional
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Anthropic client
try:
    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
except Exception as e:
    st.error(f"❌ Failed to initialize Claude API: {e}")
    st.stop()

# Page configuration
st.set_page_config(
    page_title="RouteVerify Lite - DSNY Demo", 
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("📋 RouteVerify Lite - DSNY Demo")

# Sidebar for configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    debug_mode = st.checkbox("Debug Mode", help="Show additional processing details")
    ocr_confidence = st.slider("OCR Confidence Threshold", 0, 100, 60)

# Access PIN with session state
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    pin = st.text_input("Enter access PIN:", type="password")
    if st.button("🔓 Authenticate"):
        if pin == "dsny2025":
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Invalid PIN. Access denied.")
    st.stop()

# Main application
st.success("🔓 Authenticated successfully!")

def validate_itsa_format(itsa: str) -> bool:
    """Validate ITSA number format (basic validation)"""
    # Basic ITSA format validation - adjust regex as needed
    pattern = r'^[A-Z0-9]{4,10}$'
    return bool(re.match(pattern, itsa.strip().upper()))

def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from PDF using PyPDF"""
    try:
        reader = PdfReader(file_path)
        all_text = ""
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                all_text += f"\n--- Page {page_num + 1} ---\n{text}"
        return all_text.strip()
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""

def extract_text_with_ocr(file_path: str, confidence_threshold: int = 60) -> str:
    """Extract text using OCR with confidence filtering"""
    try:
        images = convert_from_path(file_path, dpi=300)  # Higher DPI for better OCR
        text = ""
        for i, image in enumerate(images):
            # Get OCR data with confidence scores
            ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            
            # Filter by confidence
            filtered_text = []
            for j, conf in enumerate(ocr_data['conf']):
                if int(conf) > confidence_threshold:
                    word = ocr_data['text'][j].strip()
                    if word:
                        filtered_text.append(word)
            
            page_text = ' '.join(filtered_text)
            if page_text:
                text += f"\n--- OCR Page {i + 1} ---\n{page_text}"
        
        return text.strip()
    except Exception as e:
        logger.error(f"OCR extraction failed: {e}")
        return ""

def process_route_sheet_with_claude(text: str) -> Optional[Dict]:
    """Process route sheet text with Claude API"""
    try:
        prompt = f'''Analyze this DS-659 route sheet text and extract the following information.
Be very careful to identify all ITSA numbers - they may be scattered throughout the document.

Return ONLY a valid JSON object with this exact structure:
{{
  "section": "section_number_or_name",
  "route": "route_number",
  "truck_number": "truck_identifier", 
  "itsas": ["itsa1", "itsa2", "itsa3"],
  "extraction_confidence": "high|medium|low",
  "notes": "any_relevant_observations"
}}

Document text:
{text}
'''
        
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )
        
        raw_response = ""
        for block in msg.content:
            if block.type == "text":
                raw_response = block.text.strip()
        
        # Try to extract JSON from the response
        json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if json_match:
            json_str = json_match.group()
            return json.loads(json_str)
        else:
            return json.loads(raw_response)
            
    except json.JSONDecodeError as e:
        st.error(f"❌ Claude returned invalid JSON: {e}")
        st.code(raw_response)
        return None
    except Exception as e:
        st.error(f"❌ Claude API error: {e}")
        return None

def simulate_gps_verification(itsas: List[str], gps_data: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Simulate GPS verification of ITSA numbers"""
    data = []
    
    for i, itsa in enumerate(itsas):
        # Validate ITSA format
        is_valid_format = validate_itsa_format(itsa)
        
        # Simulate verification based on various factors
        if not is_valid_format:
            status = "❌ Invalid Format"
            notes = "ITSA number format is invalid"
        elif i % 3 == 0:
            status = "✅ Verified"
            notes = f"GPS match at {10 + i*2}:{'15' if i%2 else '45'} AM"
        elif i % 3 == 1:
            status = "⚠️ Partial"
            notes = "GPS coverage limited in this area"
        else:
            status = "❌ Missed"
            notes = "No GPS coverage detected"
        
        data.append({
            "ITSA": itsa,
            "Status": status,
            "Valid_Format": "✅" if is_valid_format else "❌",
            "Notes": notes,
            "Timestamp": f"2025-07-24 {10 + i}:{'30' if i%2 else '00'}:00"
        })
    
    return pd.DataFrame(data)

# File upload sections
col1, col2 = st.columns(2)

with col1:
    st.header("📄 Upload DS-659 Route Sheet")
    route_file = st.file_uploader(
        "Upload Route Sheet", 
        type=["pdf", "jpg", "jpeg", "png"],
        help="Supported formats: PDF, JPG, PNG"
    )

with col2:
    st.header("📍 Upload Rastrac GPS Trail")
    gps_file = st.file_uploader(
        "Upload Rastrac GPS File", 
        type=["csv"],
        help="CSV file with GPS tracking data"
    )

# Process route sheet
claude_json = {}
if route_file:
    with st.spinner("🔍 Processing route sheet..."):
        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{route_file.name.split('.')[-1]}") as tmp:
            tmp.write(route_file.getvalue())
            tmp_path = tmp.name
        
        try:
            # Extract text
            st.info("📖 Extracting text from document...")
            route_text = extract_text_from_pdf(tmp_path) if route_file.type == "application/pdf" else ""
            
            # Fallback to OCR if needed
            if not route_text.strip():
                st.info("🔍 Using OCR for text extraction...")
                route_text = extract_text_with_ocr(tmp_path, ocr_confidence)
            
            if not route_text.strip():
                st.error("❌ No readable text found in document.")
                st.stop()
            
            if debug_mode:
                with st.expander("📝 Extracted Text (Debug)"):
                    st.text_area("Raw extracted text:", route_text, height=200)
            
            # Process with Claude
            st.info("🤖 Analyzing with Claude AI...")
            claude_json = process_route_sheet_with_claude(route_text)
            
            if claude_json:
                st.success("✅ Route sheet processed successfully!")
                
                # Display extracted information
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Section", claude_json.get("section", "N/A"))
                with col2:
                    st.metric("Route", claude_json.get("route", "N/A"))
                with col3:
                    st.metric("Truck", claude_json.get("truck_number", "N/A"))
                
                st.metric("ITSAs Found", len(claude_json.get("itsas", [])))
                
                if debug_mode:
                    with st.expander("🔍 Claude Analysis (Debug)"):
                        st.json(claude_json)
            
        except Exception as e:
            st.error(f"❌ Processing failed: {e}")
            logger.error(f"Route sheet processing error: {e}")
        finally:
            # Clean up temporary file
            try:
                os.unlink(tmp_path)
            except:
                pass

# Process GPS file
gps_data = None
if gps_file:
    try:
        gps_data = pd.read_csv(gps_file)
        st.success(f"✅ GPS file loaded: {len(gps_data)} records")
        
        if debug_mode:
            with st.expander("📊 GPS Data Preview (Debug)"):
                st.dataframe(gps_data.head())
    except Exception as e:
        st.error(f"❌ Failed to load GPS file: {e}")

# Generate verification report
if claude_json and isinstance(claude_json, dict) and "itsas" in claude_json:
    st.header("🧪 SmartScan+ Verification Report")
    
    itsas = claude_json["itsas"]
    if not itsas:
        st.warning("⚠️ No ITSA numbers found in route sheet.")
    else:
        # Generate verification results
        verification_df = simulate_gps_verification(itsas, gps_data)
        
        # Display summary metrics
        total_itsas = len(verification_df)
        verified_count = len(verification_df[verification_df["Status"].str.contains("✅")])
        missed_count = len(verification_df[verification_df["Status"].str.contains("❌")])
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total ITSAs", total_itsas)
        with col2:
            st.metric("Verified", verified_count, delta=f"{verified_count/total_itsas*100:.1f}%")
        with col3:
            st.metric("Missed", missed_count, delta=f"-{missed_count/total_itsas*100:.1f}%")
        with col4:
            completion_rate = verified_count / total_itsas * 100 if total_itsas > 0 else 0
            st.metric("Completion Rate", f"{completion_rate:.1f}%")
        
        # Display detailed results
        st.dataframe(verification_df, use_container_width=True)
        
        # Download options
        col1, col2 = st.columns(2)
        with col1:
            csv_data = verification_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Download Verification Report (CSV)",
                data=csv_data,
                file_name=f"verification_report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
        
        with col2:
            # Generate summary report
            summary_report = f"""
RouteVerify Lite - Verification Summary
Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}

Route Information:
- Section: {claude_json.get('section', 'N/A')}
- Route: {claude_json.get('route', 'N/A')}
- Truck: {claude_json.get('truck_number', 'N/A')}

Verification Results:
- Total ITSAs: {total_itsas}
- Verified: {verified_count} ({verified_count/total_itsas*100:.1f}%)
- Missed: {missed_count} ({missed_count/total_itsas*100:.1f}%)
- Completion Rate: {completion_rate:.1f}%

ITSA Details:
{chr(10).join([f"- {row['ITSA']}: {row['Status']} - {row['Notes']}" for _, row in verification_df.iterrows()])}
"""
            st.download_button(
                "📋 Download Summary Report (TXT)",
                data=summary_report,
                file_name=f"summary_report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain"
            )

# Footer
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666;'>
    <p>Built for NYC DSNY Supervisors • RouteVerify Lite v2.0 • Enhanced Claude OCR Processing</p>
    <p>⚡ Powered by Claude AI • 🔒 Secure Processing • 📊 Real-time Analysis</p>
</div>
""", unsafe_allow_html=True)

# Logout button in sidebar
with st.sidebar:
    st.markdown("---")
    if st.button("🚪 Logout"):
        st.session_state.authenticated = False
        st.rerun()

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
    st.error(f"Failed to initialize Claude API: {e}")
    st.stop()

# Page configuration
st.set_page_config(
    page_title="RouteVerify Lite - DSNY",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("RouteVerify Lite - DSNY SI03")

# Sidebar
with st.sidebar:
    st.header("Configuration")
    debug_mode = st.checkbox("Debug Mode", help="Show additional processing details")
    ocr_confidence = st.slider("OCR Confidence Threshold", 0, 100, 60)

# PIN authentication
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    pin = st.text_input("Enter access PIN:", type="password")
    if st.button("Authenticate"):
        if pin == "dsny2025":
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid PIN. Access denied.")
    st.stop()

st.success("Authenticated")


# ─── TEXT EXTRACTION ────────────────────────────────────────────────────────

def extract_text_from_pdf(file_path: str) -> str:
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
    try:
        images = convert_from_path(file_path, dpi=300)
        text = ""
        for i, image in enumerate(images):
            ocr_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
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


# ─── CLAUDE: ROUTE SHEET PARSING ────────────────────────────────────────────

def process_route_sheet_with_claude(text: str) -> Optional[Dict]:
    try:
        prompt = (
            "Analyze this DS-659 DSNY route sheet and extract structured data.\n\n"
            "Return ONLY a valid JSON object with this exact structure:\n"
            "{\n"
            '  "section": "section_number",\n'
            '  "route": "route_number",\n'
            '  "district": "district_code",\n'
            '  "material": "material_description",\n'
            '  "itsas": [\n'
            '    {"number": 1, "street": "STREET NAME", "from_cross": "FROM STREET", "to_cross": "TO STREET", "side": "B"}\n'
            "  ],\n"
            '  "extraction_confidence": "high|medium|low"\n'
            "}\n\n"
            "IMPORTANT: Extract every ITSA entry with its street name and cross streets.\n"
            "Use uppercase for street names. Side values: B=Both, R=Right, L=Left.\n\n"
            "Document text:\n" + text
        )

        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_response = ""
        for block in msg.content:
            if block.type == "text":
                raw_response = block.text.strip()

        json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(raw_response)

    except json.JSONDecodeError as e:
        st.error(f"Claude returned invalid JSON: {e}")
        return None
    except Exception as e:
        st.error(f"Claude API error: {e}")
        return None


# ─── GPS CSV PARSING ─────────────────────────────────────────────────────────

def parse_rastrac_csv(gps_df: pd.DataFrame) -> set:
    """
    Extract unique street names from Rastrac CSV 'Address' column.
    Address format: "123 Street Name, Staten Island, NY, 10312"
    Returns a set of normalized street name strings.
    """
    streets_visited = set()

    if 'Address' not in gps_df.columns:
        # Try alternate column names
        addr_col = None
        for col in gps_df.columns:
            if 'addr' in col.lower() or 'street' in col.lower() or 'location' in col.lower():
                addr_col = col
                break
        if not addr_col:
            return streets_visited
    else:
        addr_col = 'Address'

    for addr in gps_df[addr_col].dropna():
        addr_str = str(addr).strip()
        # Remove house number (leading digits and spaces)
        # "123 Philip Ave, Staten Island, NY, 10312" -> "Philip Ave"
        parts = addr_str.split(',')
        if parts:
            street_part = parts[0].strip()
            # Remove leading house number
            street_only = re.sub(r'^\d+\s+', '', street_part).strip().upper()
            if street_only:
                streets_visited.add(street_only)

    return streets_visited


def normalize_street(name: str) -> str:
    """Normalize street name for comparison."""
    name = name.upper().strip()
    # Common abbreviation expansions
    replacements = {
        ' AVE': ' AVE', ' AVENUE': ' AVE',
        ' ST': ' ST', ' STREET': ' ST',
        ' BLVD': ' BLVD', ' BOULEVARD': ' BLVD',
        ' DR': ' DR', ' DRIVE': ' DR',
        ' CT': ' CT', ' COURT': ' CT',
        ' PL': ' PL', ' PLACE': ' PL',
        ' RD': ' RD', ' ROAD': ' RD',
        ' LN': ' LN', ' LANE': ' LN',
        ' TER': ' TER', ' TERRACE': ' TER',
        ' HWY': ' HWY', ' HIGHWAY': ' HWY',
    }
    for full, abbr in replacements.items():
        name = name.replace(full.strip(), abbr.strip())
    return name


def verify_itsas_against_gps(itsas: List[Dict], streets_visited: set) -> pd.DataFrame:
    """
    Match each ITSA street against the GPS-visited streets.
    Returns a DataFrame with verification results.
    """
    rows = []
    norm_visited = {normalize_street(s) for s in streets_visited}

    for itsa in itsas:
        num = itsa.get('number', '?')
        street = itsa.get('street', '').strip()
        from_cross = itsa.get('from_cross', '')
        to_cross = itsa.get('to_cross', '')
        side = itsa.get('side', 'B')

        norm_street = normalize_street(street)

        # Check exact match first
        matched = norm_street in norm_visited

        # Fuzzy: check if any visited street contains the key word(s)
        if not matched:
            street_words = set(norm_street.split())
            for visited in norm_visited:
                visited_words = set(visited.split())
                # At least 2 words match (handles "PHILIP AVE" vs "PHILIP AVENUE")
                if len(street_words & visited_words) >= min(2, len(street_words)):
                    matched = True
                    break

        if matched:
            status = "DONE"
            status_icon = "✅"
        else:
            status = "SKIPPED"
            status_icon = "❌"

        maps_link = (
            "https://www.google.com/maps/dir/My+Location/"
            + street.replace(' ', '+') + ",+Staten+Island,+NY+10312"
        )

        rows.append({
            "ITSA #": num,
            "Street": street,
            "From": from_cross,
            "To": to_cross,
            "Side": side,
            "Status": status_icon + " " + status,
            "Navigate": maps_link if status == "SKIPPED" else ""
        })

    return pd.DataFrame(rows)


# ─── MAIN UI ─────────────────────────────────────────────────────────────────

col1, col2 = st.columns(2)

with col1:
    st.header("Step 1: Upload DS-659 Route Sheet")
    route_file = st.file_uploader(
        "Route Sheet (PDF or photo)",
        type=["pdf", "jpg", "jpeg", "png"],
        help="Upload the DS-659 route narrative"
    )

with col2:
    st.header("Step 2: Upload Rastrac GPS CSV")
    gps_file = st.file_uploader(
        "Rastrac GPS Export (CSV)",
        type=["csv"],
        help="Export GPS History from Rastrac for this truck/date"
    )

# Store results in session state
if 'claude_json' not in st.session_state:
    st.session_state.claude_json = None
if 'gps_streets' not in st.session_state:
    st.session_state.gps_streets = None
if 'gps_df' not in st.session_state:
    st.session_state.gps_df = None

# ── Process Route Sheet ──
if route_file:
    with st.spinner("Processing route sheet with AI..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix="." + route_file.name.split('.')[-1]) as tmp:
            tmp.write(route_file.getvalue())
            tmp_path = tmp.name

        try:
            route_text = extract_text_from_pdf(tmp_path) if route_file.type == "application/pdf" else ""
            if not route_text.strip():
                route_text = extract_text_with_ocr(tmp_path, ocr_confidence)

            if not route_text.strip():
                st.error("No readable text found in document.")
            else:
                if debug_mode:
                    with st.expander("Extracted Text (Debug)"):
                        st.text_area("Raw text:", route_text, height=200)

                result = process_route_sheet_with_claude(route_text)
                if result:
                    st.session_state.claude_json = result
                    st.success(
                        f"Route sheet processed: "
                        f"Section {result.get('section','?')} | "
                        f"Route {result.get('route','?')} | "
                        f"{len(result.get('itsas',[]))} ITSAs found"
                    )
                    if debug_mode:
                        with st.expander("Parsed Route Data (Debug)"):
                            st.json(result)
        finally:
            try:
                os.unlink(tmp_path)
            except:
                pass

# ── Process GPS File ──
if gps_file:
    try:
        gps_df = pd.read_csv(gps_file)
        st.session_state.gps_df = gps_df
        streets = parse_rastrac_csv(gps_df)
        st.session_state.gps_streets = streets
        st.success(f"GPS file loaded: {len(gps_df)} records | {len(streets)} unique streets detected")

        if debug_mode:
            with st.expander("Streets detected in GPS data (Debug)"):
                st.write(sorted(streets))
    except Exception as e:
        st.error(f"Failed to load GPS file: {e}")

# ── Run Verification ──
st.divider()

if st.session_state.claude_json and st.session_state.gps_streets:
    if st.button("Run Route Verification", type="primary"):
        itsas = st.session_state.claude_json.get('itsas', [])
        streets_visited = st.session_state.gps_streets

        if not itsas:
            st.error("No ITSAs found in route sheet. Check the uploaded file.")
        else:
            results_df = verify_itsas_against_gps(itsas, streets_visited)

            total = len(results_df)
            done = len(results_df[results_df['Status'].str.contains('DONE')])
            skipped = total - done
            pct = round((done / total) * 100, 1) if total > 0 else 0

            # Summary metrics
            st.header("Verification Results")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total ITSAs", total)
            m2.metric("Confirmed Done", done)
            m3.metric("Skipped / Missed", skipped)
            m4.metric("Completion", f"{pct}%")

            # Color-coded table
            st.subheader("ITSA Breakdown")
            st.dataframe(
                results_df[["ITSA #", "Street", "From", "To", "Side", "Status"]],
                use_container_width=True,
                height=min(600, 50 + 35 * total)
            )

            # Missed streets with nav links
            missed = results_df[results_df['Status'].str.contains('SKIPPED')]
            if not missed.empty:
                st.subheader(f"Missed Streets — Navigation Links ({len(missed)} streets)")
                st.caption("Tap any link to navigate directly (opens Google Maps)")

                # Single multi-stop link for all missed streets
                stops = "/".join(
                    row['Street'].replace(' ', '+') + ",+Staten+Island,+NY+10312"
                    for _, row in missed.iterrows()
                )
                multi_link = "https://www.google.com/maps/dir/My+Location/" + stops
                st.markdown(f"**[Navigate All Missed Streets (single route)]({multi_link})**")

                st.divider()
                for _, row in missed.iterrows():
                    st.markdown(
                        f"ITSA {row['ITSA #']} — **{row['Street']}** "
                        f"({row['From']} → {row['To']}) "
                        f"[Navigate]({row['Navigate']})"
                    )

            # Download report
            st.divider()
            report_lines = [
                "ROUTE VERIFICATION REPORT — NYC DSNY",
                f"Section: {st.session_state.claude_json.get('section','?')} | "
                f"Route: {st.session_state.claude_json.get('route','?')} | "
                f"District: {st.session_state.claude_json.get('district','?')}",
                f"Completion: {done}/{total} ITSAs ({pct}%)",
                "",
                "ITSA | STREET | FROM | TO | STATUS",
                "-" * 60
            ]
            for _, row in results_df.iterrows():
                report_lines.append(
                    f"{row['ITSA #']} | {row['Street']} | {row['From']} | {row['To']} | {row['Status']}"
                )
            if not missed.empty:
                report_lines.append("")
                report_lines.append("MISSED STREETS:")
                for _, row in missed.iterrows():
                    report_lines.append(f"  ITSA {row['ITSA #']}: {row['Street']} ({row['From']} to {row['To']})")

            report_text = "\n".join(report_lines)
            st.download_button(
                label="Download Report (.txt)",
                data=report_text,
                file_name="routeverify_report.txt",
                mime="text/plain"
            )

elif not st.session_state.claude_json:
    st.info("Upload a DS-659 route sheet to begin.")
elif not st.session_state.gps_streets:
    st.info("Upload the Rastrac GPS CSV to complete verification.")

import streamlit as st
import pandas as pd
import os
import tempfile
import base64
from dotenv import load_dotenv
import anthropic
from pypdf import PdfReader
import json
import logging
from typing import Dict, List, Optional
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

st.set_page_config(page_title="RouteVerify - DSNY", layout="wide")
st.title("RouteVerify Lite — DSNY SI03")

# Always show API key input in sidebar — env var used as default if available
_env_key = os.getenv("CLAUDE_API_KEY", "")
with st.sidebar:
    st.header("Configuration")
    debug_mode = st.checkbox("Debug Mode")
    _api_key = st.text_input(
        "Anthropic API Key",
        value=_env_key,
        type="password",
        help="Paste your sk-ant-... key here"
    )

if not _api_key or not _api_key.startswith("sk-ant"):
    st.warning("Enter your Anthropic API key in the sidebar to continue.")
    st.stop()

try:
    client = anthropic.Anthropic(api_key=_api_key)
except Exception as e:
    st.error(f"Failed to initialize Claude API: {e}")
    st.stop()

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    pin = st.text_input("Enter access PIN:", type="password")
    if st.button("Authenticate"):
        if pin == "dsny2025":
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid PIN.")
    st.stop()

st.success("Authenticated")


# ─── ROUTE SHEET PARSING (Claude Vision) ────────────────────────────────────

def compress_image(image_bytes: bytes, max_bytes: int = 4_500_000) -> tuple[bytes, str]:
    """Compress image to fit under Claude's 5MB limit. Returns (bytes, media_type)."""
    from PIL import Image
    import io
    if len(image_bytes) <= max_bytes:
        return image_bytes, "image/jpeg"
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    quality = 85
    while quality >= 30:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data, "image/jpeg"
        quality -= 10
    # If still too big, resize to half
    img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue(), "image/jpeg"


def process_image_with_claude(image_bytes: bytes, media_type: str) -> Optional[Dict]:
    """Send image directly to Claude vision — handles DS659 photos accurately."""
    try:
        image_bytes, media_type = compress_image(image_bytes)
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

        prompt = (
            "This is a DSNY DS-659 Route Narrative form. "
            "Extract ALL route information and return ONLY valid JSON.\n\n"
            "JSON structure:\n"
            "{\n"
            '  "section": "section code",\n'
            '  "route": "route number",\n'
            '  "district": "district code",\n'
            '  "material": "material description",\n'
            '  "vehicle_type": "vehicle type",\n'
            '  "itsas": [\n'
            '    {"number": 1, "street": "STREET NAME", "from_cross": "FROM", "to_cross": "TO", "side": "B"}\n'
            "  ],\n"
            '  "extraction_confidence": "high|medium|low"\n'
            "}\n\n"
            "Rules:\n"
            "- Extract EVERY ITSA row from the table\n"
            "- Use UPPERCASE for street names\n"
            "- Side: B=Both sides, R=Right, L=Left\n"
            "- If a field is unclear, use your best reading\n"
            "- Return ONLY the JSON, no other text"
        )

        msg = client.messages.create(
            model="claude-opus-4-5-20251101",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }]
        )

        raw = ""
        for block in msg.content:
            if block.type == "text":
                raw = block.text.strip()

        if debug_mode:
            with st.expander("Claude raw response (Debug)"):
                st.text(raw)

        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(raw)

    except json.JSONDecodeError as e:
        st.error(f"Claude returned invalid JSON: {e}")
        return None
    except Exception as e:
        st.error(f"Claude API error: {e}")
        return None


def process_pdf_with_claude(file_bytes: bytes) -> Optional[Dict]:
    """Extract text from PDF then send to Claude."""
    try:
        import io
        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"

        if not text.strip():
            st.warning("PDF has no extractable text — try uploading a photo instead.")
            return None

        prompt = (
            "This is DSNY DS-659 route sheet text. "
            "Extract all data and return ONLY valid JSON:\n"
            "{\n"
            '  "section": "section code",\n'
            '  "route": "route number",\n'
            '  "district": "district code",\n'
            '  "material": "material description",\n'
            '  "itsas": [\n'
            '    {"number": 1, "street": "STREET NAME", "from_cross": "FROM", "to_cross": "TO", "side": "B"}\n'
            "  ],\n"
            '  "extraction_confidence": "high|medium|low"\n'
            "}\n\nText:\n" + text
        )

        msg = client.messages.create(
            model="claude-opus-4-5-20251101",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(raw)

    except Exception as e:
        st.error(f"PDF processing error: {e}")
        return None


# ─── GPS CSV PARSING ─────────────────────────────────────────────────────────

def parse_rastrac_csv(gps_df: pd.DataFrame) -> set:
    streets_visited = set()
    addr_col = None
    for col in gps_df.columns:
        if 'addr' in col.lower() or col.lower() == 'address':
            addr_col = col
            break
    if not addr_col:
        return streets_visited

    for addr in gps_df[addr_col].dropna():
        addr_str = str(addr).strip()
        parts = addr_str.split(',')
        if parts:
            street_part = parts[0].strip()
            street_only = re.sub(r'^\d+\s+', '', street_part).strip().upper()
            if street_only:
                streets_visited.add(street_only)
    return streets_visited


def normalize_street(name: str) -> str:
    name = name.upper().strip()
    expansions = {
        'AVENUE': 'AVE', 'STREET': 'ST', 'BOULEVARD': 'BLVD',
        'DRIVE': 'DR', 'COURT': 'CT', 'PLACE': 'PL',
        'ROAD': 'RD', 'LANE': 'LN', 'TERRACE': 'TER',
        'HIGHWAY': 'HWY', 'PARKWAY': 'PKWY'
    }
    for full, abbr in expansions.items():
        name = re.sub(r'\b' + full + r'\b', abbr, name)
    return name.strip()


def verify_itsas_against_gps(itsas: List[Dict], streets_visited: set) -> pd.DataFrame:
    rows = []
    norm_visited = {normalize_street(s) for s in streets_visited}

    for itsa in itsas:
        num = itsa.get('number', '?')
        street = str(itsa.get('street', '')).strip()
        from_cross = itsa.get('from_cross', '')
        to_cross = itsa.get('to_cross', '')
        side = itsa.get('side', 'B')

        norm_street = normalize_street(street)
        matched = norm_street in norm_visited

        if not matched:
            street_words = set(norm_street.split())
            for visited in norm_visited:
                visited_words = set(visited.split())
                overlap = street_words & visited_words
                if len(overlap) >= min(2, len(street_words)):
                    matched = True
                    break

        status = "✅ DONE" if matched else "❌ SKIPPED"
        maps_link = (
            "https://www.google.com/maps/dir/My+Location/"
            + street.replace(' ', '+') + ",+Staten+Island,+NY+10312"
        ) if not matched else ""

        rows.append({
            "ITSA #": num,
            "Street": street,
            "From": from_cross,
            "To": to_cross,
            "Side": side,
            "Status": status,
            "_link": maps_link
        })

    return pd.DataFrame(rows)


# ─── MAIN UI ─────────────────────────────────────────────────────────────────

col1, col2 = st.columns(2)

with col1:
    st.header("Step 1 — DS-659 Route Sheet")
    route_file = st.file_uploader(
        "Upload route sheet photo or PDF",
        type=["jpg", "jpeg", "png", "pdf"],
        help="Take a photo of the DS-659 or upload a PDF"
    )

with col2:
    st.header("Step 2 — Rastrac GPS CSV")
    gps_file = st.file_uploader(
        "Upload Rastrac GPS export",
        type=["csv"],
        help="Export GPS History from Rastrac for this truck and date"
    )

if 'claude_json' not in st.session_state:
    st.session_state.claude_json = None
if 'gps_streets' not in st.session_state:
    st.session_state.gps_streets = None

# Process route sheet
if route_file:
    with st.spinner("Reading route sheet with Claude AI..."):
        file_bytes = route_file.read()
        ext = route_file.name.split('.')[-1].lower()

        if ext == 'pdf':
            result = process_pdf_with_claude(file_bytes)
        else:
            media_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'}
            media_type = media_map.get(ext, 'image/jpeg')
            result = process_image_with_claude(file_bytes, media_type)

        if result:
            st.session_state.claude_json = result
            n = len(result.get('itsas', []))
            sec = result.get('section', '?')
            route = result.get('route', '?')
            conf = result.get('extraction_confidence', '?')
            st.success(f"Route sheet read: Section {sec} | Route {route} | {n} ITSAs | Confidence: {conf}")

            if debug_mode:
                with st.expander("Parsed route data"):
                    st.json(result)

# Process GPS file
if gps_file:
    try:
        gps_df = pd.read_csv(gps_file)
        streets = parse_rastrac_csv(gps_df)
        st.session_state.gps_streets = streets
        st.success(f"GPS file loaded: {len(gps_df)} records | {len(streets)} unique streets")
        if debug_mode:
            with st.expander("Streets in GPS data"):
                st.write(sorted(streets))
    except Exception as e:
        st.error(f"Failed to load GPS file: {e}")

# Run verification
st.divider()

if st.session_state.claude_json and st.session_state.gps_streets:
    if st.button("▶ Run Route Verification", type="primary"):
        itsas = st.session_state.claude_json.get('itsas', [])
        if not itsas:
            st.error("No ITSAs found in route sheet.")
        else:
            df = verify_itsas_against_gps(itsas, st.session_state.gps_streets)
            total = len(df)
            done = len(df[df['Status'].str.contains('DONE')])
            skipped = total - done
            pct = round(done / total * 100, 1) if total > 0 else 0

            st.header("Results")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total ITSAs", total)
            c2.metric("Done", done)
            c3.metric("Skipped", skipped)
            c4.metric("Completion", f"{pct}%")

            # Progress bar
            st.progress(done / total if total > 0 else 0)

            st.subheader("ITSA Breakdown")
            st.dataframe(
                df[["ITSA #", "Street", "From", "To", "Side", "Status"]],
                use_container_width=True,
                height=min(700, 50 + 35 * total)
            )

            # Missed streets
            missed = df[df['Status'].str.contains('SKIPPED')]
            if not missed.empty:
                st.subheader(f"Missed Streets — {len(missed)} to service")

                stops = "/".join(
                    r['Street'].replace(' ', '+') + ",+Staten+Island,+NY+10312"
                    for _, r in missed.iterrows()
                )
                multi = "https://www.google.com/maps/dir/My+Location/" + stops
                st.markdown(f"**[Navigate All Missed Streets →]({multi})**")
                st.divider()

                for _, r in missed.iterrows():
                    st.markdown(
                        f"ITSA **{r['ITSA #']}** — {r['Street']} "
                        f"({r['From']} → {r['To']}) "
                        f"[Navigate]({r['_link']})"
                    )

            # Download
            st.divider()
            info = st.session_state.claude_json
            lines = [
                "ROUTE VERIFICATION REPORT — NYC DSNY",
                f"Section: {info.get('section','?')} | Route: {info.get('route','?')} | District: {info.get('district','?')}",
                f"Completion: {done}/{total} ITSAs ({pct}%)",
                "", "─" * 50
            ]
            for _, r in df.iterrows():
                lines.append(f"ITSA {r['ITSA #']} | {r['Street']} | {r['From']} → {r['To']} | {r['Status']}")
            if not missed.empty:
                lines += ["", "MISSED:"]
                for _, r in missed.iterrows():
                    lines.append(f"  ITSA {r['ITSA #']}: {r['Street']} ({r['From']} → {r['To']})")

            st.download_button(
                "Download Report",
                data="\n".join(lines),
                file_name="routeverify_report.txt",
                mime="text/plain"
            )

elif not st.session_state.claude_json:
    st.info("Upload a DS-659 route sheet to begin.")
elif not st.session_state.gps_streets:
    st.info("Upload the Rastrac GPS CSV to complete verification.")

# app.py
# Streamlit PDF Processing with robust Gemini parsing and fallbacks
# Fixes 'list' object has no attribute 'get' by normalizing AI output and adding text-based fallback.

import os
import io
import re
import json
from datetime import datetime
from decimal import Decimal
from typing import Optional, Union

import streamlit as st
from PyPDF2 import PdfReader, PdfWriter
import fitz  # PyMuPDF
from PIL import Image

# Google Gemini
import google.generativeai as genai

# ---------------------------
# Streamlit page config
# ---------------------------
st.set_page_config(page_title="PDF Processing & Email System", page_icon="📄", layout="centered")

st.title("PDF Processing & Email System")

# ---------------------------
# Configure Gemini (expects GOOGLE_API_KEY in env)
# ---------------------------
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not GOOGLE_API_KEY:
    st.warning("GOOGLE_API_KEY not set in environment. Set it before running to enable Gemini extraction.")
genai.configure(api_key=GOOGLE_API_KEY)

# ---------------------------
# Ensure directories and session
# ---------------------------
if 'processed_files' not in st.session_state:
    st.session_state.processed_files = []

TEMP_DIR = 'temp'
OUTPUT_FOLDER = 'output'
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# ---------------------------
# Helpers
# ---------------------------
def sanitize_filename(filename: str) -> str:
    invalid_chars = r'[<>:"/\\|?*]'
    return re.sub(invalid_chars, '_', filename).strip() or "untitled"

# Special mappings for simplified_name
SPECIAL_MAP = {
    'FH-CAPDYN:MF/BOC': 'CAPDYN',
    'FH-Mirae': 'Mirae',
    'FH-iFund': 'iFund',
    'FH-GaoTeng': 'GaoTeng',
    'FH-GF-MMF': 'GF',
    'FH-TaiKang': 'TaiKang',
    'CMB Wing Lung': 'GF MMF',
    'ICBC(Asia) Trustee - GaoTeng': 'GaoTeng',
    'BOCI-Prudential Trustee - Taikang Kaitai': 'Taikang',
    'Webull Securities': 'Webull',
    'JPMorgan Bank Luxembourg SA - Momentum': 'Momentum',
    'BOCI Prudential Asset Management Limited': 'BOCIP',
    'FH-Peak/Belgrave': 'Belgrave',
    'FH-Everbright/Broker': 'Everbright',
    'FH-NJ/': 'Nanjia',
}

def simplify_from_full(full_name: str) -> str:
    if not full_name:
        return ""
    for key, val in SPECIAL_MAP.items():
        if key in full_name:
            return val
    # Rule: take text before the first dash, if any
    return full_name.split('-', 1)[0].strip()

def normalize_ai_results(raw: Union[dict, list, str, None]) -> Optional[dict]:
    """
    Normalize Gemini output to a single dict.
    Accepts dict, list[dict], or JSON string. Returns dict or None.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                return item
        return None
    if isinstance(raw, str):
        # Sometimes the SDK returns .text containing JSON or fenced code
        s = raw.strip()
        if s.startswith("```"):
            # strip code fences
            if "```json" in s:
                s = s.split("```json", 1)[1]
            else:
                s = s.split("```", 1)[1]
            s = s.split("```", 1)[0]
        try:
            data = json.loads(s)
            return normalize_ai_results(data)
        except Exception:
            return None
    return None

def convert_pdf_to_image(pdf_path: str, page_num: int) -> Optional[Image.Image]:
    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        zoom = 4  # quality scaler
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_data = pix.tobytes("png")
        doc.close()
        return Image.open(io.BytesIO(img_data))
    except Exception as e:
        st.error(f"PDF conversion error (page {page_num+1}): {str(e)}")
        return None

def is_summary_page(text: str) -> bool:
    summary_indicators = [
        "Summary",
        "Grand Total",
        "Currency\nFDS_190.rpt\n",
        "Total\n"
    ]

    if any(ind in text for ind in summary_indicators):
        if "Payment Group" not in text:
            return True

    if "Summary" in text and any(curr in text for curr in ["JPY", "USD", "HKD", "AUD", "EUR", "GBP", "CNY"]):
        lines = text.split('\n')
        currency_total_count = 0
        for line in lines:
            if re.search(r'Total\s*(JPY|USD|HKD|AUD|EUR|GBP|CNY)\s*[\d,]+\.\d{2}', line):
                currency_total_count += 1
        if currency_total_count >= 2:
            return True

    return False

def fallback_extract_from_text(page_text: str, context: dict) -> Optional[dict]:
    """
    Fallback when model output is missing or incomplete.
    Extracts currency and payment total via regex and reuses previous page's fund house if needed.
    """
    data: dict = {}

    # Currency
    mcur = re.search(r'Currency\s*:\s*(USD|HKD|JPY|AUD|EUR|GBP|CNY)', page_text)
    if mcur:
        data["currency"] = mcur.group(1).strip()
    elif context.get("currency"):
        data["currency"] = context["currency"]

    # Full name (Fund Hse Settlement Inst :)
    mfull = re.search(r'Fund Hse Settlement Inst\s*:\s*(.+)', page_text)
    if mfull:
        full = mfull.group(1).strip()
        data["full_name"] = full
        data["simplified_name"] = simplify_from_full(full)
    else:
        # Continuation page: carry forward
        if context.get("full_name"):
            data["full_name"] = context["full_name"]
        if context.get("simplified_name"):
            data["simplified_name"] = context["simplified_name"]

    # Payment Group Total
    mpay = re.search(r'Payment Group\s+\S+\s+Total\s+([\d,]+\.\d{2})', page_text)
    if mpay:
        data["payment_total"] = mpay.group(1).strip()

    if data.get("simplified_name") and data.get("currency") and data.get("payment_total"):
        data["confidence"] = "MEDIUM" if mfull else "LOW"
        return data
    return None

def get_gemini_response(image: Image.Image) -> Optional[dict]:
    """
    Ask Gemini to extract a single JSON object. Normalizes any list outputs.
    """
    try:
        model = genai.GenerativeModel(
            'gemini-2.0-flash',
            generation_config={
                "temperature": 0,
                "response_mime_type": "application/json"
            }
        )

        prompt = """
Extract information from the document image and return ONE JSON OBJECT only (not an array), with these exact keys:
{
  "full_name": "...",
  "simplified_name": "...",
  "currency": "...",
  "payment_total": "...",
  "confidence": "HIGH|MEDIUM|LOW"
}

Rules:
1) Use the exact text after 'Fund Hse Settlement Inst :' as full_name.
   When computing simplified_name, if full_name contains '-', keep only the part BEFORE the first '-',
   unless any special mapping below applies.
2) Special mappings (override simplified_name if applicable):
   FH-CAPDYN:MF/BOC → CAPDYN
   FH-Mirae → Mirae
   FH-iFund → iFund
   FH-GaoTeng → GaoTeng
   FH-GF-MMF → GF
   FH-TaiKang → TaiKang
   CMB Wing Lung → GF MMF
   ICBC(Asia) Trustee - GaoTeng → GaoTeng
   BOCI-Prudential Trustee - Taikang Kaitai → Taikang
   Webull Securities → Webull
   JPMorgan Bank Luxembourg SA - Momentum → Momentum
   BOCI Prudential Asset Management Limited → BOCIP
   FH-Peak/Belgrave → Belgrave
   FH-Everbright/Broker → Everbright
   FH-NJ/ → Nanjia
3) currency is the value in 'Currency :'.
4) payment_total is the numeric string after 'Payment Group XXXX Total'.
5) If the page is a continuation and 'Fund Hse Settlement Inst :' is not visible, infer from visible text and set confidence to LOW if uncertain.
Return only the JSON object, no markdown or explanation.
        """.strip()

        response = model.generate_content([prompt, image])
        if not response or not getattr(response, "text", None):
            return None

        data = normalize_ai_results(response.text)
        return data
    except Exception as e:
        st.error(f"Gemini API error: {str(e)}")
        return None

def process_pdf(uploaded_file, start_sequence: int, progress_bar) -> list:
    generated_files = []
    temp_path = None

    try:
        temp_path = os.path.join(TEMP_DIR, uploaded_file.name)
        with open(temp_path, 'wb') as f:
            f.write(uploaded_file.getbuffer())

        # Use both fitz (for text/image) and PyPDF2 (for page writing)
        doc = fitz.open(temp_path)
        reader = PdfReader(temp_path)
        sequence_number = start_sequence
        total_pages = len(doc)

        # Context carries fund house and currency across continuation pages
        context = {"full_name": None, "simplified_name": None, "currency": None}

        for page_number in range(total_pages):
            progress = (page_number + 1) / total_pages
            try:
                progress_bar.progress(progress, text=f"Processing page {page_number + 1} of {total_pages}")
            except TypeError:
                # Older Streamlit versions
                progress_bar.progress(progress)

            page = doc[page_number]
            page_text = page.get_text()

            # Skip summary/total page(s)
            if is_summary_page(page_text):
                continue

            # Try vision extraction first
            page_image = convert_pdf_to_image(temp_path, page_number)
            ai_results = get_gemini_response(page_image) if page_image is not None else None
            ai_results = normalize_ai_results(ai_results)

            # Fallback to text parsing if needed
            if not ai_results or not all(
                ai_results.get(k) for k in ("simplified_name", "currency", "payment_total")
            ):
                ai_results = fallback_extract_from_text(page_text, context)

            if not ai_results:
                st.warning(f"Skipping page {page_number + 1}: could not extract required fields.")
                continue

            # Update context so continuation pages can reuse info
            context["full_name"] = ai_results.get("full_name") or context["full_name"]
            context["simplified_name"] = ai_results.get("simplified_name") or context["simplified_name"]
            context["currency"] = ai_results.get("currency") or context["currency"]

            chosen_name = ai_results.get("simplified_name") or simplify_from_full(ai_results.get("full_name", ""))
            currency = ai_results.get("currency")
            payment_total = ai_results.get("payment_total")

            if chosen_name and currency and payment_total:
                date_str = datetime.now().strftime('%y%m%d')
                sanitized_name = sanitize_filename(chosen_name)
                filename = f"S{date_str}-{str(sequence_number).zfill(2)}_{sanitized_name}_{currency}-order details.pdf"
                output_path = os.path.join(OUTPUT_FOLDER, filename)

                # Write a single-page PDF for this page
                try:
                    pdf_writer = PdfWriter()
                    pdf_writer.add_page(reader.pages[page_number])
                    with open(output_path, 'wb') as output_file:
                        pdf_writer.write(output_file)
                except Exception as e:
                    st.error(f"Error writing output PDF for page {page_number + 1}: {str(e)}")
                    continue

                # Convert payment_total to number if possible
                try:
                    pay_num = Decimal(payment_total.replace(',', ''))
                except Exception:
                    pay_num = None

                with open(output_path, 'rb') as file:
                    file_content = file.read()
                    generated_files.append({
                        'filename': filename,
                        'content': file_content,
                        'currency': currency,
                        'payment_total': float(pay_num) if pay_num is not None else None
                    })

                sequence_number += 1

        doc.close()
        try:
            progress_bar.progress(1.0, text="Processing complete!")
        except TypeError:
            progress_bar.progress(1.0)
        return generated_files

    except Exception as e:
        st.error(f"Processing error: {str(e)}")
        return []
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                st.warning(f"Could not remove temporary file: {str(e)}")

# ---------------------------
# Simple UI
# ---------------------------
with st.expander("1. PDF Processing", expanded=True):
    last_used = st.number_input("Last sequence number used:", min_value=0, step=1, value=0)
    uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])
    col1, col2 = st.columns([1, 2])
    with col1:
        start_seq = st.number_input("Start sequence:", min_value=1, step=1, value=last_used + 1)
    run = st.button("Process PDF and Create Email Drafts")

    if run:
        if not uploaded_file:
            st.error("Please upload a PDF first.")
        else:
            progress_bar = st.progress(0.0)
            results = process_pdf(uploaded_file, int(start_seq), progress_bar)

            if results:
                st.success(f"Generated {len(results)} file(s).")
                # Show a quick summary
                st.write([
                    {
                        "filename": r["filename"],
                        "currency": r["currency"],
                        "payment_total": r["payment_total"],
                    }
                    for r in results
                ])

                # Aggregate by currency
                totals = {}
                for r in results:
                    cur = r["currency"]
                    amt = r["payment_total"] or 0.0
                    totals[cur] = totals.get(cur, 0.0) + amt
                if totals:
                    st.subheader("Aggregated totals by currency")
                    for cur, amt in totals.items():
                        st.write(f"{cur}: {amt:,.2f}")
            else:
                st.info("No output files were generated.")

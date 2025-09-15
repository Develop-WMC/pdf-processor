import streamlit as st
import os
from PyPDF2 import PdfReader, PdfWriter
import fitz  # PyMuPDF
import google.generativeai as genai
import json
from PIL import Image
import io
from datetime import datetime
import re

# --- Page Configuration ---
st.set_page_config(layout="wide", page_title="PDF Processing System")

# --- Main App Title ---
st.title("PDF Processing & Email System")

# --- API Key Configuration (The Correct, Secure Way) ---
# This securely accesses the API key from Streamlit's secrets management.
# Ensure you have a GOOGLE_API_KEY="your_key_here" in your secrets.toml file
# or in the app settings on Streamlit Community Cloud.
try:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except (KeyError, AttributeError):
    st.error("API Key not found. Please add your Google Gemini API Key to your Streamlit secrets.")
    st.info("Add a secret like this: `GOOGLE_API_KEY = 'YOUR_API_KEY_HERE'`")
    st.stop()


# --- Session State and Directory Setup ---
if 'processed_files' not in st.session_state:
    st.session_state.processed_files = []

TEMP_DIR = 'temp'
OUTPUT_FOLDER = 'output'
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


# --- Core Functions ---

def sanitize_filename(filename):
    """Removes invalid characters from a string to make it a valid filename."""
    return re.sub(r'[<>:"/\\|?*]', '_', filename)

def get_gemini_response(image):
    """
    Sends a PDF page image to the Gemini model and robustly parses the JSON response.
    This function is now designed to handle both JSON objects and arrays gracefully.
    """
    try:
        model = genai.GenerativeModel('gemini-pro-vision')
        prompt = """Analyze the document image. Your goal is to extract specific fields.
        The document is a fund house settlement report.

        - If the page is a primary settlement page, find these fields:
          1.  "Fund Hse Settlement Inst :": Extract the text. If it contains a dash ('-'), use only the part BEFORE the first dash (e.g., "State Street Fund Services (Ireland) Limited - Barings" -> "Barings"). Special Case: "MFEX - BlackRock" -> "MFEX".
          2.  "Currency :": Extract the 3-letter currency code (e.g., USD).
          3.  "Payment Group ... Total": Extract the final numerical total for the payment group.

        - If the page is a continuation page, summary page, or does not contain these specific fields, you do not need to find them.

        **RESPONSE FORMAT**:
        - For primary settlement pages, RETURN ONLY A JSON OBJECT like this:
          {"simplified_name": "Barings", "currency": "AUD", "payment_total": "10551.97"}
        - For all other pages (continuation, summary, etc.), RETURN AN EMPTY JSON OBJECT:
          {}
        """
        response = model.generate_content([prompt, image])
        
        # --- ROBUST JSON EXTRACTION (FIXED) ---
        json_str = response.text
        match = re.search(r"\{.*\}", json_str, re.DOTALL)
        
        if match:
            return json.loads(match.group(0))
        else:
            return {}

    except json.JSONDecodeError:
        st.warning("AI returned malformed JSON. The page will be skipped.")
        return {} # Return empty dict on JSON parsing failure
    except Exception as e:
        st.error(f"An error occurred with the AI model: {str(e)}")
        return None # Return None for other critical errors

def convert_pdf_to_image(pdf_path, page_num):
    """Converts a single PDF page to a high-resolution PIL Image for analysis."""
    try:
        doc = fitz.open(pdf_path)
        if page_num >= len(doc): return None
        page = doc[page_num]
        zoom = 4.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_data = pix.tobytes("png")
        doc.close()
        return Image.open(io.BytesIO(img_data))
    except Exception as e:
        st.error(f"Error converting PDF page {page_num + 1} to image: {str(e)}")
        return None

def process_pdf(uploaded_file, start_sequence, progress_bar, status_area):
    """
    Main processing loop. Iterates through PDF pages, calls the AI, and creates
    individual PDF files. Includes robust error checking.
    """
    generated_files = []
    temp_path = None

    try:
        temp_path = os.path.join(TEMP_DIR, uploaded_file.name)
        with open(temp_path, 'wb') as f:
            f.write(uploaded_file.getbuffer())

        reader = PdfReader(temp_path)
        total_pages = len(reader.pages)
        sequence_number = start_sequence

        for page_number in range(total_pages):
            status_text = f"Processing page {page_number + 1} of {total_pages}"
            progress_bar.progress((page_number + 1) / total_pages, text=status_text)
            status_area.text(status_text)

            page_image = convert_pdf_to_image(temp_path, page_number)
            if not page_image:
                status_area.warning(f"Could not convert page {page_number + 1}. Skipping.")
                continue

            ai_results = get_gemini_response(page_image)

            # --- ROBUST CHECKING (FIXED) ---
            # This check ensures the result is a non-empty dictionary, which correctly
            # handles the original "'list' object has no attribute 'get'" error.
            if not isinstance(ai_results, dict) or not ai_results:
                status_area.info(f"Page {page_number + 1} is a continuation/summary page or lacks data. Skipping.")
                continue

            chosen_name = ai_results.get("simplified_name")
            currency = ai_results.get("currency")
            payment_total = ai_results.get("payment_total")

            if chosen_name and currency and payment_total:
                date_str = datetime.now().strftime('%y%m%d')
                sanitized_name = sanitize_filename(chosen_name)
                filename = f"S{date_str}-{str(sequence_number).zfill(2)}_{sanitized_name}_{currency}-order details.pdf"
                output_path = os.path.join(OUTPUT_FOLDER, filename)

                pdf_writer = PdfWriter()
                pdf_writer.add_page(reader.pages[page_number])
                
                with open(output_path, 'wb') as output_file:
                    pdf_writer.write(output_file)

                with open(output_path, 'rb') as file:
                    file_content = file.read()
                    generated_files.append({
                        'filename': filename,
                        'content': file_content,
                    })
                
                status_area.success(f"✓ Generated file for page {page_number + 1}: {filename}")
                sequence_number += 1
            else:
                status_area.info(f"Page {page_number + 1} was analyzed but didn't contain all required fields. Skipping.")

        status_area.success("Processing complete!")
        return generated_files, sequence_number

    except Exception as e:
        st.error(f"A critical error occurred: {str(e)}")
        return [], start_sequence
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                st.warning(f"Could not remove temporary file: {e}")


# --- Streamlit UI Layout ---

st.header("1. PDF Processing")

col1, col2 = st.columns([1, 4])
with col1:
    last_sequence = st.number_input("Last sequence number used:", min_value=0, value=0, step=1, key="seq_num")

uploaded_file = st.file_uploader(
    "Upload PDF",
    type="pdf",
    help="Drag and drop your settlement PDF file (limit 200MB)."
)

if uploaded_file:
    st.info(f"File ready: `{uploaded_file.name}`")

    if st.button("Process PDF and Create Email Drafts", type="primary"):
        st.session_state.processed_files = []
        progress_bar = st.progress(0, "Initializing...")
        with st.expander("Processing Log", expanded=True):
            status_area = st.empty()
            status_area.text("Starting PDF processing...")
            processed_results, next_sequence = process_pdf(uploaded_file, last_sequence + 1, progress_bar, status_area)
        
        progress_bar.empty()

        if processed_results:
            st.session_state.processed_files = processed_results
            st.success(f"**Processing finished. {len(processed_results)} files were generated.**")
            st.info(f"The next sequence number should be: **{next_sequence}**")
        else:
            st.error("No valid settlement pages were found to generate files.")

# --- Display Generated Files for Download ---
if st.session_state.processed_files:
    st.header("2. Generated Files")
    st.markdown("---")
    for item in st.session_state.processed_files:
        st.download_button(
            label=f"⬇️ Download: {item['filename']}",
            data=item['content'],
            file_name=item['filename'],
            mime='application/pdf'
        )

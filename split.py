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

# --- Configuration ---
# It's recommended to set your API key as an environment variable
# or using Streamlit's secrets management.
# For demonstration, we'll use st.text_input, but this is not secure for production.
st.set_page_config(layout="wide")

st.title("PDF Processing & Email System")

# Securely get the API key
api_key = st.text_input("Enter your Google Gemini API Key", type="password")
if api_key:
    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        st.error(f"Failed to configure Gemini API: {e}")
        st.stop()
else:
    st.warning("Please enter your Google Gemini API Key to proceed.")
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
    """Removes characters that are invalid for filenames."""
    invalid_chars = r'[<>:"/\\|?*]'
    return re.sub(invalid_chars, '_', filename)

def get_gemini_response(image):
    """
    Sends an image of a PDF page to the Gemini model and asks it to extract
    specific information in a JSON format.
    """
    try:
        # Using a more recent and capable model is recommended
        model = genai.GenerativeModel('gemini-pro-vision')
        
        prompt = """From the document image, extract the following information.
        The document is a fund house settlement report. Each page represents a settlement for a fund house, except for continuation or summary pages.

        1.  **Fund Hse Settlement Inst**: Find the text following "Fund Hse Settlement Inst :".
            - If it contains a dash ('-'), take only the part BEFORE the first dash.
            - Special Cases (use the name after the arrow):
              - "ICBC(Asia) Trustee Company Limited - GaoTeng" -> "GaoTeng"
              - "State Street Fund Services (Ireland) Limited - Barings" -> "Barings"
              - "UI efa S.A. - Nevastar" -> "Nevastar"
              - "MFEX - BlackRock" -> "MFEX"

        2.  **Currency**: Find the 3-letter code following "Currency :".

        3.  **Payment Group Total**: Find the numerical value following "Payment Group [ID] Total".

        If you cannot find these fields (e.g., on a continuation or summary page), return an empty JSON object.

        RETURN A JSON OBJECT. Your response must be only the JSON, like this:
        {
            "simplified_name": "Extracted and simplified name",
            "currency": "e.g., USD",
            "payment_total": "e.g., 31510.97"
        }
        """

        response = model.generate_content([prompt, image])
        
        # Robustly extract JSON from the response text
        json_str = response.text
        match = re.search(r"```(json)?\s*(\{.*?\})\s*```", json_str, re.DOTALL)
        if match:
            json_str = match.group(2)
        else:
            # Fallback for cases where markdown is not used but JSON is present
            json_str = json_str[json_str.find('{'):json_str.rfind('}')+1]

        # Return None if the cleaned string is empty or not valid JSON
        if not json_str.strip():
             return {} # Return an empty dict if no JSON is found

        return json.loads(json_str.strip())

    except Exception as e:
        st.error(f"An error occurred with the AI model: {str(e)}")
        return None

def convert_pdf_to_image(pdf_path, page_num):
    """Converts a single page of a PDF into a high-resolution PIL Image."""
    try:
        doc = fitz.open(pdf_path)
        if page_num >= len(doc):
            return None
        page = doc[page_num]
        zoom = 4  # Increase zoom for better OCR quality
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_data = pix.tobytes("png")
        doc.close()
        return Image.open(io.BytesIO(img_data))
    except Exception as e:
        st.error(f"Error converting PDF page to image: {str(e)}")
        return None

def is_continuation_or_summary_page(page_text):
    """
    Checks if a page is a summary or a continuation page that should be skipped.
    """
    # Summary pages often have "Summary" and totals for multiple currencies
    if "Summary" in page_text and "Payment Group" not in page_text:
        return True
    
    # Continuation pages lack the main header but might have the footer
    if "Fund House :" not in page_text and "WMC Nominees Ltd" in page_text:
        return True
        
    return False

def process_pdf(uploaded_file, start_sequence, progress_bar, status_area):
    """
    Main processing function. Iterates through a PDF, extracts data from each page,
    and creates individual PDF files for each valid settlement.
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
            progress = (page_number + 1) / total_pages
            status_text = f"Processing page {page_number + 1} of {total_pages}"
            progress_bar.progress(progress, status_text)
            status_area.text(status_text)
            
            # Check if page should be skipped early
            page_text = reader.pages[page_number].extract_text()
            if is_continuation_or_summary_page(page_text):
                status_area.info(f"Page {page_number + 1} identified as a summary/continuation page. Skipping.")
                continue

            # Convert page to image for AI processing
            page_image = convert_pdf_to_image(temp_path, page_number)
            if not page_image:
                status_area.warning(f"Could not convert page {page_number + 1} to an image. Skipping.")
                continue

            ai_results = get_gemini_response(page_image)

            if ai_results:
                # ======================= FIX APPLIED HERE =======================
                # The original error was "'list' object has no attribute 'get'".
                # This check ensures we only proceed if the AI returns a dictionary,
                # not a list or another data type.
                if not isinstance(ai_results, dict):
                    status_area.warning(f"Unexpected data format received from AI for page {page_number + 1}. Skipping.")
                    continue
                # ================================================================

                chosen_name = ai_results.get("simplified_name")
                currency = ai_results.get("currency")
                payment_total = ai_results.get("payment_total")

                # Proceed only if all required fields were extracted
                if chosen_name and currency and payment_total:
                    date_str = datetime.now().strftime('%y%m%d')
                    sanitized_name = sanitize_filename(chosen_name)
                    filename = f"S{date_str}-{str(sequence_number).zfill(2)}_{sanitized_name}_{currency}-order details.pdf"
                    output_path = os.path.join(OUTPUT_FOLDER, filename)

                    # Create a new PDF with just the current page
                    pdf_writer = PdfWriter()
                    pdf_writer.add_page(reader.pages[page_number])
                    
                    with open(output_path, 'wb') as output_file:
                        pdf_writer.write(output_file)

                    # Read the content of the newly created file for download
                    with open(output_path, 'rb') as file:
                        file_content = file.read()
                        generated_files.append({
                            'filename': filename,
                            'content': file_content,
                            'currency': currency,
                            'payment_total': float(str(payment_total).replace(',', ''))
                        })
                    
                    status_area.success(f"Successfully processed page {page_number + 1} -> {filename}")
                    sequence_number += 1
                else:
                    status_area.info(f"Page {page_number + 1} did not contain all required data (Name, Currency, Total). Skipping.")

        progress_bar.empty()
        status_area.success("Processing complete!")
        return generated_files, sequence_number

    except Exception as e:
        st.error(f"A critical error occurred during PDF processing: {str(e)}")
        return [], start_sequence
    finally:
        # Clean up the temporary file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                st.warning(f"Could not remove temporary file: {str(e)}")


# --- Streamlit UI Layout ---

st.header("1. PDF Processing")

col1, col2 = st.columns([1, 4])
with col1:
    last_sequence = st.number_input("Last sequence number used:", min_value=0, value=0, step=1)

uploaded_file = st.file_uploader(
    "Upload PDF",
    type="pdf",
    help="Drag and drop or browse for your settlement PDF file."
)

if uploaded_file:
    st.info(f"Uploaded: `{uploaded_file.name}` ({uploaded_file.size / 1024:.1f} KB)")

    if st.button("Process PDF and Create Email Drafts"):
        st.session_state.processed_files = []
        progress_bar = st.progress(0, "Starting processing...")
        status_area = st.empty()

        with st.spinner('Processing PDF... This may take a few moments.'):
            processed_results, next_sequence = process_pdf(uploaded_file, last_sequence + 1, progress_bar, status_area)
            if processed_results:
                st.session_state.processed_files = processed_results
                st.success(f"Successfully processed the PDF and generated {len(processed_results)} files.")
                st.info(f"The next sequence number to use is: **{next_sequence}**")
            else:
                st.error("No valid settlement pages could be processed from the PDF.")
        
        # Clear progress bar and status text after completion
        progress_bar.empty()
        status_area.empty()


if st.session_state.processed_files:
    st.header("2. Generated Files")
    st.write("Download the generated PDF files below.")
    for item in st.session_state.processed_files:
        st.download_button(
            label=f"⬇️ Download {item['filename']}",
            data=item['content'],
            file_name=item['filename'],
            mime='application/pdf'
        )

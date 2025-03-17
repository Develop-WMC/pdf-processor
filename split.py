import streamlit as st
import os
from PyPDF2 import PdfReader, PdfWriter
import fitz
import google.generativeai as genai
import json
from PIL import Image
import io
from datetime import datetime
import re

# Setup directories and session state
if 'processed_files' not in st.session_state:
    st.session_state.processed_files = []

TEMP_DIR = 'temp'
OUTPUT_FOLDER = 'output'
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

def sanitize_filename(filename):
    invalid_chars = r'[<>:"/\\|?*]'
    return re.sub(invalid_chars, '_', filename)

def get_gemini_response(image):
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        prompt = """Extract information from the document:

        1. From 'Fund Hse Settlement Inst :' field:
           - If contains dash (-): take part BEFORE the first dash
           - Example: "ABC - XYZ" → use "ABC"
        
        2. For special patterns:
           FH-CAPDYN:MF/BOC → CAPDYN
           FH-Mirae → Mirae
           FH-iFund → iFund
           FH-GaoTeng → GaoTeng
           FH-GF-MMF → GF
           FH-TaiKang → TaiKang
        
        3. For specific cases:
           - CMB Wing Lung → GF MMF
           - ICBC(Asia) Trustee - GaoTeng → GaoTeng
           - BOCI-Prudential Trustee - Taikang Kaitai → Taikang
           - Webull Securities → Webull
           - JPMorgan Bank Luxembourg SA - Momentum → Momentum
           - BOCI Prudential Asset Management Limited → BOCIP
           - FH-Peak/Belgrave → Belgrave
           - FH-Everbright/Broker → Everbright
           - FH-NJ/ → Nanjia

        4. Extract Currency from 'Currency :' field (USD/HKD/JPY)

        5. Extract Payment Group Total amount (numerical value after 'Payment Group XXXX Total')
         
        RETURN EXACTLY (no explanation):
        {
            "full_name": "exact text from Fund Hse Settlement Inst field",
            "simplified_name": "processed name following rules above",
            "currency": "currency from Currency field",
            "payment_total": "numerical value from Payment Group Total",
            "confidence": "HIGH/MEDIUM/LOW"
        }"""

        response = model.generate_content([prompt, image])
        json_str = response.text
        
        if '```json' in json_str:
            json_str = json_str.split('```json')[1].split('```')[0]
        elif '```' in json_str:
            json_str = json_str.split('```')[1].split('```')[0]
            
        return json.loads(json_str.strip())
    except Exception as e:
        st.error(f"Gemini API error: {str(e)}")
        return None
    
def convert_pdf_to_image(pdf_path, page_num):
    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        zoom = 4
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_data = pix.tobytes("png")
        doc.close()
        return Image.open(io.BytesIO(img_data))
    except Exception as e:
        st.error(f"PDF conversion error: {str(e)}")
        return None

def is_summary_page(text):
    summary_indicators = [
        "Summary",
        "Grand Total",
        "Currency\nFDS_190.rpt\n",
        "Total\n"
    ]
    
    if any(indicator in text for indicator in summary_indicators):
        if "Payment Group" not in text:
            return True
    
    if "Summary" in text and any(curr in text for curr in ["JPY", "USD", "HKD"]):
        lines = text.split('\n')
        currency_total_count = 0
        for line in lines:
            if re.search(r'Total\s*(JPY|USD|HKD)\s*[\d,]+\.\d{2}', line):
                currency_total_count += 1
        
        if currency_total_count >= 2:
            return True
    
    return False

def process_pdf(uploaded_file, start_sequence, progress_bar):
    generated_files = []
    temp_path = None

    try:
        temp_path = os.path.join(TEMP_DIR, uploaded_file.name)
        with open(temp_path, 'wb') as f:
            f.write(uploaded_file.getbuffer())

        doc = fitz.open(temp_path)
        sequence_number = start_sequence
        total_pages = len(doc)

        for page_number in range(total_pages):
            progress = (page_number + 1) / total_pages
            progress_bar.progress(progress, f"Processing page {page_number + 1} of {total_pages}")
            
            page = doc[page_number]
            page_text = page.get_text()

            if is_summary_page(page_text):
                continue

            page_image = convert_pdf_to_image(temp_path, page_number)
            
            if page_image:
                ai_results = get_gemini_response(page_image)
                if ai_results:
                    chosen_name = ai_results.get("simplified_name")
                    currency = ai_results.get("currency")
                    payment_total = ai_results.get("payment_total")

                    if chosen_name and currency and payment_total:
                        date_str = datetime.now().strftime('%y%m%d')
                        sanitized_name = sanitize_filename(chosen_name)
                        filename = f"S{date_str}-{str(sequence_number).zfill(2)}_{sanitized_name}_{currency}-order details.pdf"
                        output_path = os.path.join(OUTPUT_FOLDER, filename)

                        pdf_writer = PdfWriter()
                        reader = PdfReader(temp_path)
                        pdf_writer.add_page(reader.pages[page_number])
                        
                        with open(output_path, 'wb') as output_file:
                            pdf_writer.write(output_file)

                        with open(output_path, 'rb') as file:
                            file_content = file.read()
                            generated_files.append({
                                'filename': filename,
                                'content': file_content,
                                'currency': currency,
                                'payment_total': float(payment_total.replace(',', ''))
                            })
                        
                        sequence_number += 1

        doc.close()
        progress_bar.progress(1.0, "Processing complete!")
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

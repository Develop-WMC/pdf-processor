import streamlit as st
import os
from datetime import datetime
from split import process_pdf
from email_draft import get_gmail_service, create_settlement_template, create_draft_email
import google.generativeai as genai
import zipfile
from io import BytesIO

def initialize_app():
    """Initialize the application settings and directories"""
    if 'processed_files' not in st.session_state:
        st.session_state.processed_files = []
    
    # Create necessary directories
    os.makedirs('temp', exist_ok=True)
    os.makedirs('output', exist_ok=True)
    
    # Configure Gemini API
    genai.configure(api_key=st.secrets["pdf_processor"]["api_key"])

def create_zip_file(files):
    """Create a ZIP file containing the provided files"""
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_info in files:
            zip_file.writestr(file_info['filename'], file_info['content'])
    return zip_buffer.getvalue()

def main():
    st.title("PDF Processing & Email System")
    initialize_app()

    # PDF Processing Section
    st.header("1. PDF Processing")

    # Sequence Number Input
    last_sequence = st.number_input(
        "Last sequence number used:",
        min_value=0,
        value=0,
        step=1
    )
    start_sequence = last_sequence + 1 if last_sequence > 0 else 1

    # File Upload
    uploaded_file = st.file_uploader("Upload PDF", type=['pdf'])

    if uploaded_file:
        if st.button("Process PDF and Create Email Drafts"):
            progress_bar = st.progress(0, "Starting processing...")
            with st.spinner("Processing PDF..."):
                generated_files = process_pdf(uploaded_file, start_sequence, progress_bar)
                if generated_files:
                    st.session_state.processed_files = generated_files
                    st.success(f"Successfully processed {len(generated_files)} files!")

                    # Gmail Service initialization
                    service = get_gmail_service()
                    if not service:
                        st.error("Gmail service initialization failed")
                        return

                    # Create email drafts for each processed file
                    for file_info in generated_files:
                        try:
                            # Create email template using extracted values
                            subject, body, html_body = create_settlement_template(
                                file_info['currency'], 
                                file_info['payment_total']
                            )
                            email_data = {
                                'subject': subject,
                                'body': body,
                                'html_body': html_body
                            }

                            # Create draft with single processed file
                            draft_id = create_draft_email(
                                service=service,
                                files=[file_info],  # Pass single file as list
                                email_data=email_data
                            )

                            if draft_id:
                                st.success(f"Email draft created successfully for {file_info['filename']}!")
                            else:
                                st.error(f"Failed to create email draft for {file_info['filename']}")

                        except Exception as e:
                            st.error(f"Error creating email draft: {str(e)}")

    # Display processed files for download
    if st.session_state.processed_files:
        st.header("2. Download Processed Files")
        
        # Download all files option
        st.subheader("Download All Files")
        all_files_zip = create_zip_file(st.session_state.processed_files)
        st.download_button(
            label=f"📥 Download All Files ({len(st.session_state.processed_files)} files)",
            data=all_files_zip,
            file_name="all_processed_files.zip",
            mime="application/zip",
            key="download_all_zip",
            use_container_width=True
        )
        
        # Display individual files grouped by currency
        st.subheader("Download by Currency")
        currency_files = {}
        for file in st.session_state.processed_files:
            curr = file['currency']
            if curr not in currency_files:
                currency_files[curr] = []
            currency_files[curr].append(file)
        
        for currency, files in currency_files.items():
            st.write(f"\n{currency} Files:")
            
            # Calculate number of columns (max 4 buttons per row)
            num_cols = min(4, len(files))
            cols = st.columns(num_cols)
            
            # Create buttons in rows
            for idx, file_info in enumerate(files):
                col_idx = idx % num_cols
                with cols[col_idx]:
                    # Create a container for consistent button styling
                    with st.container():
                        st.download_button(
                            label=file_info['filename'].replace('_order details.pdf', ''),
                            data=file_info['content'],
                            file_name=file_info['filename'],
                            mime="application/pdf",
                            key=f"download_{currency}_{idx}",
                            use_container_width=True,
                        )
                        # Add some vertical spacing between rows
                        if idx >= num_cols:
                            st.write("")
            
            # Add separator between currency groups
            st.markdown("---")

if __name__ == "__main__":
    main()

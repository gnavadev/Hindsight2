"""
File Handler Utilities

Read various document formats and extract text content
"""

import os


def read_document(file_path: str) -> str:
    """
    Read a document and extract text content.
    Supports: TXT, MD, PDF, DOCX
    
    Args:
        file_path: Path to the document
    
    Returns:
        Extracted text content
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == '.txt' or ext == '.md':
        return _read_text_file(file_path)
    elif ext == '.pdf':
        return _read_pdf(file_path)
    elif ext == '.docx':
        return _read_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _read_text_file(file_path: str) -> str:
    """Read plain text file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        # Try with different encoding
        with open(file_path, 'r', encoding='latin-1') as f:
            return f.read()


def _read_pdf(file_path: str) -> str:
    """Read PDF file and extract text"""
    try:
        from pypdf import PdfReader
        
        reader = PdfReader(file_path)
        text_parts = []
        
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            text_parts.append(f"--- Page {i+1} ---\n{text}\n")
        
        return "\n".join(text_parts)
    except Exception as e:
        return f"Error reading PDF: {str(e)}"


def _read_docx(file_path: str) -> str:
    """Read DOCX file and extract text"""
    try:
        from docx import Document
        
        doc = Document(file_path)
        text_parts = []
        
        for paragraph in doc.paragraphs:
            text_parts.append(paragraph.text)
        
        return "\n".join(text_parts)
    except Exception as e:
        return f"Error reading DOCX: {str(e)}"


def validate_file_type(file_path: str, allowed_extensions: list) -> bool:
    """
    Check if file has allowed extension.
    
    Args:
        file_path: Path to file
        allowed_extensions: List of allowed extensions (e.g., ['.pdf', '.txt'])
    
    Returns:
        True if file type is allowed
    """
    ext = os.path.splitext(file_path)[1].lower()
    return ext in allowed_extensions

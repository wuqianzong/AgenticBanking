import os
import pymupdf4llm

os.makedirs("data/raw", exist_ok=True)

print("=================================================================")
print("📄 TASK 2 (UPGRADED): RAG-OPTIMIZED MARKDOWN PDF PARSING")
print("=================================================================")

def parse_banking_pdf_markdown():
    # Using the local file you already downloaded
    pdf_path = "sample_fee_schedule.pdf"
    md_path = "data/raw/sample_fee_schedule.md"
    
    if not os.path.exists(pdf_path):
        print(f"❌ Error: Could not find {pdf_path} in the current directory.")
        return

    try:
        print(f"Analyzing block geometry and reading order for: {pdf_path}...")
        
        # pymupdf4llm automatically detects columns, tables, and headers
        # and converts them into LLM-friendly Markdown format.
        md_text = pymupdf4llm.to_markdown(pdf_path)
        
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_text)
            
        print(f"🟢 Success! Clean markdown saved to {md_path} ({len(md_text)} characters)")
        print("\n--- Snippet of the clean formatting ---")
        print(md_text[:500] + "\n...\n")
        
    except Exception as e:
        print(f"❌ PDF Parsing error: {e}")

if __name__ == "__main__":
    parse_banking_pdf_markdown()
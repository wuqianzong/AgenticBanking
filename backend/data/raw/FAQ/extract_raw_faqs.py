import json
import uuid
import re
import time
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Target Base URLs
BASE_URL = "https://trustbank.sg/faqs/"
DOMAIN_HOST = "https://trustbank.sg"

def build_real_corpus():
    real_corpus = []

    print("[*] Booting headless Chromium browser for ETL pipeline...")
    with sync_playwright() as p:
        # Launch lightweight headless browser with anti-bot evasion
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # ==========================================
        # 1. TAXONOMY DISCOVERY
        # ==========================================
        print(f"[*] Fetching master taxonomy from {BASE_URL}...")
        page.goto(BASE_URL, wait_until="networkidle") 
        soup = BeautifulSoup(page.content(), 'html.parser')

        taxonomy_links = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if "/faqs/" in href and href.strip('/') != "faqs":
                full_url = urljoin(DOMAIN_HOST, href)
                taxonomy_links.add(full_url)

        taxonomy_urls = list(taxonomy_links)
        print(f"[+] Discovered {len(taxonomy_urls)} taxonomy branches.")

        # ==========================================
        # 2. REAL DOM EXTRACTION (Tailwind/Next.js Patched)
        # ==========================================
        for url in taxonomy_urls:
            print(f"[*] Scraping real data from branch: {url}")
            try:
                page.goto(url, wait_until="networkidle")
                time.sleep(2) # Buffer for React hydration
                
                page_soup = BeautifulSoup(page.content(), 'html.parser')
                original_category = [part for part in urlparse(url).path.split('/') if part][-1]

                # PATTERN MATCHING: Find all divs where ID starts with "ans-"
                answer_containers = page_soup.find_all('div', id=lambda x: x and x.startswith('ans-'))

                for ans_div in answer_containers:
                    # 1. Extract the Answer Text
                    # Convert list items to bullet points for clean formatting
                    for li in ans_div.find_all('li'):
                        li.insert(0, "- ")
                    
                    answer_text = ans_div.get_text(separator=" ", strip=True)
                    answer_text = re.sub(r'\s+', ' ', answer_text)

                    # 2. Extract the Question Text
                    # The question is inside the preceding sibling <a> tag
                    question_container = ans_div.find_previous_sibling('div')
                    if question_container:
                        question_text = question_container.get_text(strip=True)
                    else:
                        continue # Skip if structure is malformed

                    # 3. Validate and Store
                    if len(answer_text) > 10 and len(question_text) > 10:
                        faq_id = f"faq_{str(uuid.uuid4())[:8]}"
                        
                        real_corpus.append({
                            "faq_id": faq_id,
                            "original_category": original_category,
                            "question": question_text,
                            "answer": answer_text,
                            "domain": "",          # Ready for your manual mapping
                            "intent_family": ""    # Ready for your manual mapping
                        })
                            
            except Exception as e:
                print(f"[!] Error processing {url}: {str(e)}")

        browser.close()

    # ==========================================
    # 3. SAVE THE GROUND TRUTH CORPUS
    # ==========================================
    output_file = "raw_trustbank_faqs.json"
    with open(output_file, "w", encoding='utf-8') as f:
        json.dump(real_corpus, f, indent=4, ensure_ascii=False)

    print(f"\n[SUCCESS] Offline ETL complete. {len(real_corpus)} real Q&A pairs written to {output_file}.")

if __name__ == "__main__":
    build_real_corpus()
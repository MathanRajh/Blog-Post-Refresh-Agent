from bs4 import BeautifulSoup, NavigableString
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Any

def fetch_html_selenium(url: str) -> str:
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get(url)
        time.sleep(3) 
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        html = driver.page_source
        driver.quit()
        return html
    except Exception as e:
        print(f"Selenium Error: {e}")
        return ""

def parse_html(html_content: str, source_url: str) -> List[Dict[str, Any]]:
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    base_domain = f"{urlparse(source_url).scheme}://{urlparse(source_url).netloc}"
    sections = []

    # 1. AGGRESSIVE TAG CLEANUP
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg', 'nav', 'footer', 'aside', 'form', 'button', 'header']):
        tag.decompose()

    junk_patterns = ['ad-', 'ads', 'popup', 'modal', 'cookie', 'subscribe', 'newsletter', 'share', 'social', 'comment', 'sidebar', 'widget', 'related', 'recommended']
    
    for tag in soup.find_all(True):
        if not hasattr(tag, 'attrs') or tag.attrs is None: continue
        try:
            classes = tag.get('class', [])
            ids = tag.get('id', '')
            if any(p in str(c).lower() for c in classes for p in junk_patterns) or \
               any(p in str(ids).lower() for p in junk_patterns):
                tag.decompose()
        except: continue

    # 2. EXTRACT TITLE & METADATA
    title_text = ""
    meta_text = ""
    h1 = soup.find('h1')
    if h1:
        title_text = h1.get_text(strip=True)
    
    for meta in soup.find_all(['span', 'div', 'p', 'time']):
        txt = meta.get_text(strip=True)
        # Look for dates or author bylines
        if len(txt) < 100 and any(x in txt.lower() for x in ['by ', 'published', 'feb ', 'jan ', '202']):
            if txt != title_text:
                meta_text += txt + " | "

    if title_text:
        sections.append({
            "heading": "Title & Metadata",
            "content": f"<h1>{title_text}</h1>\n<p><em>{meta_text.strip(' | ')}</em></p>",
            "level": "header",
            "links": []
        })

    # 3. LOCATE MAIN CONTENT
    main_content = soup.find('article') or soup.find(role='main')
    if not main_content:
        divs = soup.find_all('div')
        if divs:
            main_content = max(divs, key=lambda t: len(t.find_all('p')))
    if not main_content:
        main_content = soup.body

    # 4. EXTRACT SECTIONS
    if main_content:
        # === FOOTER CHOPPER ===
        # Remove paragraphs that look like footer links (Terms, Privacy, Copyright)
        footer_keywords = ["copyright", "rights reserved", "terms of use", "privacy policy", "contact us", "about us", "newsletter", "subscribe"]
        for p in main_content.find_all(['p', 'div', 'span']):
            txt = p.get_text(strip=True).lower()
            if len(txt) < 150 and any(k in txt for k in footer_keywords):
                p.decompose()

        # === DYNAMIC MAIN HEADING DETECTION ===
        # User Rule:
        # 1. >1 h1 => h1 is main
        # 2. <=1 h1 AND >1 h2 => h2 is main
        # 3. <=1 h1 AND <=1 h2 AND >1 h3 => h3 is main
        
        h1_count = len(main_content.find_all('h1'))
        h2_count = len(main_content.find_all('h2'))
        h3_count = len(main_content.find_all('h3'))
        
        split_tags = []
        if h1_count > 1:
            split_tags = ['h1']
        elif h2_count > 1:
            split_tags = ['h2']
        elif h3_count > 1:
            split_tags = ['h3']
        else:
            # Fallback: strict hierarchy failed, look for any structure
            split_tags = ['h2', 'h3']

        # Determine "Introduction" level based on what we found (cosmetic)
        intro_level = split_tags[0] if split_tags else "h2"
        current_section = {"heading": "Introduction", "content": "", "level": intro_level, "links": []}
        
        def process_node(node):
            if not node or isinstance(node, NavigableString): return
            
            # CLEANUP INSIDE NODE BEFORE EXTRACTING
            # We want to keep <a>, <strong>, <em>, <b>, <i>, <span> but remove others?
            # Actually, let's just keep the text and links.
            
            # Extract links for the database "links" array
            for a in node.find_all('a', href=True):
                try:
                    full_url = urljoin(base_domain, a['href'])
                    if not full_url.startswith(('javascript', 'mailto')):
                        current_section["links"].append({
                            "text": a.get_text(strip=True)[:150],
                            "url": full_url
                        })
                except: pass

            # PRESERVE HTML FOR CONTENT (Critical for LLM to see links)
            # We clone the node to avoid modifying the original soup during iteration if needed, 
            # but here we can modify `node` since we are processing it.
            
            # 1. Unwrap styles/spans that might cluster
            for tag in node.find_all(['span', 'div', 'font']):
                tag.unwrap()
                
            # 2. Get HTML content with links
            # decode_contents() returns the inner HTML string
            try:
                content_html = node.decode_contents().strip()
            except:
                content_html = node.get_text(strip=True)
            
            # Remove empty tags or excessive whitespace
            if len(node.get_text(strip=True)) > 20:
                # We wrap in <p> but keep inner <a> tags
                current_section["content"] += f"<p>{content_html}</p>\n"

        search_targets = split_tags + ['p']
        if split_tags:
            for element in main_content.find_all(search_targets):
                if element.name in split_tags:
                    if current_section["content"].strip():
                        sections.append(current_section)
                    current_section = {
                        "heading": element.get_text(strip=True), 
                        "content": f"<{element.name}>{element.get_text(strip=True)}</{element.name}>", 
                        "level": element.name,
                        "links": []
                    }
                elif element.name == 'p':
                    process_node(element)
            if current_section["content"].strip():
                sections.append(current_section)
        else:
            for p in main_content.find_all('p'):
                process_node(p)
            if current_section["content"].strip():
                sections.append(current_section)

    return sections

def scrape_url_structured(url: str) -> List[Dict[str, Any]]:
    html = fetch_html_selenium(url)
    if not html: raise Exception("Failed to load page.")
    sections = parse_html(html, url)
    if not sections: raise Exception("No content found.")
    return sections
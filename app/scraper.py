from bs4 import BeautifulSoup, NavigableString
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Any, Optional

def detect_main_heading_level(soup: BeautifulSoup) -> Optional[str]:
    """
    Deterministic heading-level detection based on frequency and hierarchy.
    """
    h1_count = len(soup.find_all('h1'))
    h2_count = len(soup.find_all('h2'))
    h3_count = len(soup.find_all('h3'))

    # STEP 2 — Primary Rule (Multi-Occurrence Detection)
    if h1_count > 1:
        return "h1"
    elif h2_count > 1:
        return "h2"
    elif h3_count > 1:
        return "h3"

    # STEP 3 — Hierarchical Fallback Rule
    if h1_count == 1:
        return "h1"
    elif h1_count == 0 and h2_count == 1:
        return "h2"
    elif h1_count == 0 and h2_count == 0 and h3_count == 1:
        return "h3"
    
    return None

def fetch_html_selenium(url: str) -> str:
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(30)
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
    
    # 0. REMOVE TOP UTILITY/MENU BARS (Aggressive Text Check)
    # Often "Home Blog RSS" appears in a small UL/DIV at the start.
    # We check the first few elements.
    for top_elem in soup.find_all(['ul', 'div', 'nav'], limit=5):
        txt = top_elem.get_text(" ", strip=True).lower()
        # Heuristic: Short text containing menu keywords
        if len(txt) < 50 and any(w in txt for w in ['home', 'blog', 'rss', 'login', 'signup', 'menu']):
            top_elem.decompose()

    # 1. SURGICAL CLEANUP
    # NOTE: 'header' removed — many blogs wrap content in <header> tags!
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg', 'footer', 'form', 'button', 'aside',
                      'img', 'video', 'audio', 'picture', 'source', 'canvas']):
        tag.decompose()
    
    # Remove only site-level headers (nav bars), not article headers
    for h in soup.find_all('header'):
        # Only decompose if it looks like a site nav (short text, has nav links)
        h_text = h.get_text(strip=True)
        if len(h_text) < 200 and not h.find(['h2', 'h3', 'h4', 'h5', 'h6']):
            h.decompose()

    # Remove non-content sections (match EXACT class tokens, not substrings)
    garbage_classes = {'related-posts', 'related_posts', 'suggested-posts', 'social-share', 'social-links',
                       'sidebar', 'author-bio', 'author-box', 'breadcrumb', 'breadcrumbs', 'nav-links', 
                       'utility-bar', 'share-buttons', 'sharing-buttons', 'comments-area', 'comment-list',
                       'subscribe-box', 'newsletter-box', 'cookie-notice', 'cookie-banner'}
    garbage_id_keywords = {'related', 'sidebar', 'breadcrumb', 'social-share', 'comments', 'cookie-notice'}
    
    for garbage in soup.find_all(lambda t: (
        any(cls.lower() in garbage_classes for cls in (t.get('class') or [])) or
        (t.get('id') or '').lower() in garbage_id_keywords
    )):
        garbage.decompose()

    # Remove figure/figcaption (images/videos) from content
    for fig in soup.find_all(['figure', 'figcaption']):
        fig.decompose()

    # 2. ROBUST MAIN CONTENT LOCATOR
    # 2. ROBUST MAIN CONTENT LOCATOR
    # Priority: article -> role="main" -> main tag -> Largest Text Block
    candidates = soup.find_all(['article', 'main'])
    candidates.extend(soup.find_all(role='main'))
    
    main_content = None
    
    # Filter candidates by text length (avoid empty/nav placeholders)
    valid_candidates = [t for t in candidates if len(t.get_text(strip=True)) > 500]
    
    if valid_candidates:
        # Pick the one with the most text
        main_content = max(valid_candidates, key=lambda t: len(t.get_text(strip=True)))
    
    # Fallback: Find the <div> or <section> with the most p/h tags or text
    if not main_content:
        potential_bodies = soup.find_all(['div', 'section'])
        if potential_bodies:
             # Score by length of text
             main_content = max(potential_bodies, key=lambda t: len(t.get_text(strip=True)))
    
    if not main_content or len(main_content.get_text(strip=True)) < 200:
        main_content = soup.body
    
    print(f"DEBUG: Main Content Tag: <{main_content.name if main_content else 'None'}>, Text Length: {len(main_content.get_text(strip=True)) if main_content else 0}")

    # 2b. CAPTURE PAGE TITLE (h1)
    # Search: inside main_content → previous siblings → page-wide
    page_title = None
    if main_content:
        h1_in_main = main_content.find('h1')
        if h1_in_main:
            page_title = h1_in_main.get_text(strip=True)
            h1_in_main.decompose()  # Remove so it doesn't get processed as a section
    if not page_title:
        # Look for h1 before main_content (common pattern: h1 is sibling before article)
        for sibling in (main_content or soup.body).find_all_previous('h1'):
            page_title = sibling.get_text(strip=True)
            break
    if not page_title:
        # Last resort: any h1 on the page
        h1_any = soup.find('h1')
        if h1_any:
            page_title = h1_any.get_text(strip=True)
    if not page_title:
        # Use <title> tag as ultimate fallback
        title_tag = soup.find('title')
        if title_tag:
            page_title = title_tag.get_text(strip=True).split('|')[0].split('-')[0].strip()
    
    print(f"DEBUG: Page Title: '{page_title}'")

    # 3. DETERMINISTIC MAIN HEADING DETECTION
    target_tag = detect_main_heading_level(main_content)
    
    # DEBUG: Print detection stats
    h1_c = len(main_content.find_all('h1'))
    h2_c = len(main_content.find_all('h2'))
    h3_c = len(main_content.find_all('h3'))
    print(f"DEBUG: Heading Counts -> h1: {h1_c}, h2: {h2_c}, h3: {h3_c}")
    print(f"DEBUG: Detected Main Heading Level: '{target_tag}'")
            
    # If still None, default to h2 or fallback logic
    split_tags = [target_tag] if target_tag else ['h2']

    # FIXED: Default heading is None (no "Introduction" forced)
    current_section = {"heading": None, "content": "", "level": target_tag or "h2", "links": [], "id_attr": None}
    
    # Pre-calculate base URL for comparison
    parsed_source = urlparse(source_url)
    source_base = f"{parsed_source.scheme}://{parsed_source.netloc}{parsed_source.path}".rstrip('/')

    def process_node(node, section):
        # Extract links including #anchor links (ToC support)
        for a in node.find_all('a', href=True):
            href = a['href']
            try:
                # Resolve full URL
                full_url = urljoin(source_url, href)
                parsed_full = urlparse(full_url)
                full_base = f"{parsed_full.scheme}://{parsed_full.netloc}{parsed_full.path}"
                
                # Check for Self-Link (Internal Anchor disguised as absolute URL)
                full_base_normalized = full_base.rstrip('/')
                if not href.startswith('#') and full_base_normalized == source_base and parsed_full.fragment:
                    # Convert to relative anchor for consistency
                    href = f"#{parsed_full.fragment}"
                    full_url = href  # FIX: Store as anchor, not full URL
                    a['href'] = href
                
                is_anchor = href.startswith('#')
                
                if not full_url.startswith(('javascript', 'mailto')):
                    section["links"].append({
                        "text": a.get_text(strip=True)[:150],
                        "url": full_url,
                        "is_anchor": is_anchor
                    })
            except: pass

        # Clean tiny inline wrappers to prevent word-splitting (e.g. T<span>he</span> -> T he)
        for tag in node.find_all(['span', 'font', 'ins', 'strong', 'b', 'em', 'i', 'mark', 'small', 'abbr']):
            tag.unwrap()
            
        try:
            # Keep structural tags intact (headings, lists, tables, etc.)
            if node.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'table', 'blockquote', 'pre', 'dl']:
                 content_html = str(node)
            else:
                 # Skip nodes that are mostly empty after cleanup
                 text_content = node.get_text(strip=True)
                 if len(text_content) < 3:
                     return
                 content_html = node.decode_contents().strip()
                 if len(text_content) > 2:
                     content_html = f"<div>{content_html}</div>"
            
            section["content"] += f"{content_html}\n"
        except: pass

    # Track if the original article had a ToC
    had_toc = False

    # 3b. PRE-PARSE: Strip ToC containers from main_content
    # Many blogs wrap their ToC in a <div>, <nav>, or <aside> with a recognizable class/id
    toc_keywords = {'toc', 'table-of-contents', 'tableofcontents', 'table_of_contents', 'post-toc', 
                    'entry-toc', 'ez-toc', 'lwptoc', 'wp-block-table-of-contents', 'rank-math-toc'}
    for container in list(main_content.find_all(['div', 'nav', 'aside'])):
        # Skip if already destroyed (child of a previously decomposed parent)
        if not container.attrs and not container.name:
            continue
        # Check by class or id
        el_classes = ' '.join(container.get('class', []) or []).lower()
        el_id = (container.get('id') or '').lower()
        if any(kw in el_classes or kw in el_id for kw in toc_keywords):
            print(f"SCRAPER: Removing ToC container <{container.name} class='{el_classes}' id='{el_id}'>")
            container.decompose()
            had_toc = True
            continue
        
        # Check by content: if a div/nav has mostly internal anchor links, it's likely a ToC
        links = container.find_all('a', href=True)
        if len(links) >= 3:
            anchor_links = [a for a in links if a.get('href', '').startswith('#') or 
                           (urlparse(a.get('href', '')).fragment and urlparse(a.get('href', '')).netloc == parsed_source.netloc)]
            if len(anchor_links) >= len(links) * 0.6:
                # Make sure this isn't a main content section (check text-to-link ratio)
                total_text = len(container.get_text(strip=True))
                link_text = sum(len(a.get_text(strip=True)) for a in links)
                if total_text > 0 and link_text / total_text > 0.7:
                    print(f"SCRAPER: Removing ToC-like container <{container.name}> ({len(anchor_links)}/{len(links)} anchors)")
                    container.decompose()
                    had_toc = True

    # 4. PARSING LOOP
    # IMPORTANT: DO NOT include 'div' or 'section' in search_targets!
    # Wrapper divs consume all content before headings can split, or trigger
    # the double-processing check that blocks their child paragraphs.
    # Only search for LEAF content elements + headings.
    search_targets = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'table', 'blockquote', 'pre', 'dl']
    found_elements = main_content.find_all(search_targets, recursive=True)
    
    for element in found_elements:
        if element.name in split_tags:
            if current_section["content"].strip():
                sections.append(current_section)
            
            # Capture the actual ID attribute to prevent breaking ToC jumps
            node_id = element.get('id')
            if not node_id:
                node_id = (element.find(id=True) or {}).get('id')
            if not node_id and element.parent and element.parent.name in ['div', 'section', 'article']:
                 node_id = element.parent.get('id')
            if not node_id:
                prev = element.find_previous_sibling()
                if prev and prev.name == 'a' and (prev.get('id') or prev.get('name')):
                    node_id = prev.get('id') or prev.get('name')
            
            current_section = {
                "heading": element.get_text(strip=True), 
                "content": "", 
                "level": element.name,
                "id_attr": node_id,
                "links": []
            }
        else:
            # Prevent double-processing: skip if a parent (that's also a search target) already covers this
            if not any(parent.name in search_targets for parent in element.parents if parent != main_content):
                if not current_section["id_attr"]:
                     current_section["id_attr"] = element.get('id')
                process_node(element, current_section)

    if current_section["content"].strip():
        sections.append(current_section)

    # === STRIP OLD ToC FROM INTRO SECTION ===
    # The first section (heading=None) often contains the blog's inline ToC
    if sections and sections[0].get("heading") is None:
        from bs4 import BeautifulSoup as _BS
        intro_soup = _BS(sections[0]["content"], "html.parser")
        toc_removed = False
        
        # Check <ul>/<ol> with anchor links
        for ul in list(intro_soup.find_all(['ul', 'ol'])):
            lis = ul.find_all('li', recursive=False)
            if not lis or len(lis) < 2:
                continue
            anchor_count = 0
            for li in lis:
                for a in li.find_all('a', href=True):
                    href = a.get('href', '')
                    if href.startswith('#') or (urlparse(href).fragment and urlparse(href).netloc == parsed_source.netloc):
                        anchor_count += 1
                        break
            if anchor_count >= len(lis) * 0.6:
                print(f"SCRAPER: Stripping old ToC list from intro ({anchor_count}/{len(lis)} anchor items)")
                ul.decompose()
                toc_removed = True
                had_toc = True
        
        # Check <div>/<nav> containers with anchor links (ToC in a wrapper)
        for container in list(intro_soup.find_all(['div', 'nav', 'aside'])):
            links = container.find_all('a', href=True)
            if len(links) >= 2:
                anchor_links = [a for a in links if a.get('href', '').startswith('#') or
                               (urlparse(a.get('href', '')).fragment and urlparse(a.get('href', '')).netloc == parsed_source.netloc)]
                if len(anchor_links) >= len(links) * 0.6:
                    print(f"SCRAPER: Stripping ToC container from intro ({len(anchor_links)}/{len(links)} anchor links)")
                    container.decompose()
                    toc_removed = True
                    had_toc = True
        
        if toc_removed:
            sections[0]["content"] = str(intro_soup)
            # Also remove ToC links from section links list
            sections[0]["links"] = [l for l in sections[0]["links"] if not l.get("is_anchor", False)]
        
        # If intro section is now empty or trivial, remove it
        remaining_text = _BS(sections[0]["content"], "html.parser").get_text(strip=True)
        if len(remaining_text) < 20:
            print("SCRAPER: Intro section empty after ToC removal, skipping")
            sections.pop(0)

    # === FALLBACK: If no sections found but we have main_content ===
    if not sections and main_content:
        print("DEBUG: No structured sections found. Using Fallback mode.")
        fallback_section = {
            "heading": "General Content",
            "content": "",
            "level": "h2",
            "links": [],
            "id_attr": "fallback-content"
        }
        
        # Extract everything from main_content
        process_node(main_content, fallback_section)
        
        # If still empty (maybe process_node failed due to depth), just grab raw HTML
        if not fallback_section["content"].strip():
            fallback_section["content"] = str(main_content)
            
        if fallback_section["content"].strip():
            sections.append(fallback_section)
    
    # DEBUG: Final summary
    total_links = sum(len(s.get('links', [])) for s in sections)
    print(f"DEBUG: Scraper Result - {len(sections)} sections, {total_links} links extracted")
    for i, sec in enumerate(sections):
        print(f"DEBUG: Section {i+1}: '{sec['heading']}' (level={sec['level']}, links={len(sec.get('links', []))})")
            
    # Prepend metadata as first item
    metadata = {"_metadata": True, "page_title": page_title, "had_toc": had_toc}
    print(f"DEBUG: Metadata -> title='{page_title}', had_toc={had_toc}")
    
    return [metadata] + sections

def scrape_url_structured(url: str) -> List[Dict[str, Any]]:
    html = fetch_html_selenium(url)
    if not html: raise Exception(f"Failed to load page: {url}")
    result = parse_html(html, url)
    if not result or len(result) < 2:  # metadata + at least 1 section
        raise Exception("No meaningful content found on page.")
    return result
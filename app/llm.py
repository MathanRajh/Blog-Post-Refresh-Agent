import google.generativeai as genai
import os
import json
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse
import re

# =========================
# GEMINI CONFIG
# =========================
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL_NAME = "gemini-2.5-flash"


# =========================
# SAFE GENERATE
# =========================
def generate_with_retry(model, prompt, output_json=False):
    max_retries = 6
    base_delay = 5.0
    last_error = None

    for attempt in range(max_retries):
        try:
            config = {"response_mime_type": "application/json"} if output_json else {}
            return model.generate_content(prompt, generation_config=config)

        except Exception as e:
            last_error = e
            msg = str(e).lower()
            print(f"GEMINI RETRY [{attempt+1}/{max_retries}]: {str(e)[:200]}")

            if "429" in msg or "resource exhausted" in msg:
                wait = base_delay * (2 ** attempt)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue

            if any(x in msg for x in ["internal", "unavailable", "timeout", "500", "503"]):
                wait = base_delay * (attempt + 1)
                print(f"  Server error, waiting {wait}s...")
                time.sleep(wait)
                continue

            print(f"  Non-retryable error: {str(e)}")
            raise

    raise RuntimeError(f"Gemini failed after {max_retries} retries. Last error: {str(last_error)}")


# =========================
# URL NORMALIZER
# =========================
def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        if url.startswith("#"):
            return url.lower().strip()

        parsed = urlparse(url)
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", parsed.fragment))
        u = clean.lower().replace("https://", "").replace("http://", "").replace("www.", "")
        return u.rstrip("/")
    except Exception:
        return url.lower().strip()


# =========================
# SLUGIFY
# =========================
def slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'\s+', '-', text)
    return text


# =========================
# MAIN HEADING DETECTION
# =========================
def detect_main_heading_level(html: str):
    soup = BeautifulSoup(html, "html.parser")

    h1_count = len(soup.find_all("h1"))
    h2_count = len(soup.find_all("h2"))
    h3_count = len(soup.find_all("h3"))

    # PRIMARY RULE (Multi-occurrence)
    if h1_count > 1:
        return "h1"
    elif h2_count > 1:
        return "h2"
    elif h3_count > 1:
        return "h3"

    # FALLBACK RULE (Hierarchy)
    if h1_count == 1:
        return "h1"
    elif h1_count == 0 and h2_count == 1:
        return "h2"
    elif h1_count == 0 and h2_count == 0 and h3_count == 1:
        return "h3"

    return None


# =========================
# AUDIT BLOG LOGIC
# =========================
# =========================
# AUDIT BLOG (HARDENED + SAFE JSON)
# =========================
def audit_blog_logic(db_sections, links_to_audit):
    """
    Hybrid audit:
    - Structure optimization (LLM) - STRICT 6 SECTION LIMIT
    - Relevance check for ALIVE links only
    - Review filtering (only return defects)
    """

    model = genai.GenerativeModel(MODEL_NAME)

    # Prepare minimal structured input
    structure_data = [
        {"id": s.id, "heading": s.heading, "level": s.level}
        for s in db_sections
    ]

    links_input = [
        {
            "id": l.id,
            "anchor": (l.text or "")[:80],
            "url": l.url
        }
        for l in links_to_audit
    ]

    # Count content sections (exclude header/title)
    # DEBUG: Show what is being counted
    all_sections = [s for s in db_sections]
    content_sections = [s for s in db_sections if s.level != "header"]
    content_sections_count = len(content_sections)
    
    print(f"DEBUG: Audit Logic - Total DB Sections: {len(all_sections)}")
    print(f"DEBUG: Audit Logic - Excluded Sections: {[s.heading for s in db_sections if s.level == 'header']}")
    print(f"DEBUG: Audit Logic - Included Content Sections: {[s.heading for s in content_sections]}")
    print(f"DEBUG: Audit Logic - Final Count: {content_sections_count}")
    print(f"DEBUG: Audit Logic - Links received for audit: {len(links_input)}")

    # Structure instruction logic
    if content_sections_count <= 6:
        structure_instruction = (
            f"NOTE: Current structure has {content_sections_count} content sections (<= 6). "
            "DO NOT propose any structure changes. Return empty list []."
        )
    else:
        excess = content_sections_count - 6
        structure_instruction = (
            f"CONSTRAINT: You have {content_sections_count} sections. The LIMIT is 6. "
            f"You MUST propose merging at least {excess} sections. Failure to do so is a CRITICAL ERROR."
        )

    prompt = f"""
You are a strict blog auditor.

GOAL 1: STRUCTURE OPTIMIZATION
- Max 6 main <h2> sections.
{structure_instruction}
- Merge small/related sections.
- Identify purely by ID.

GOAL 2: LINK RELEVANCE
- Links provided are ALIVE.
- Evaluate CONTEXTUAL RELEVANCE only.
- **DEFAULT TO VALID**. Only mark as invalid if:
  - Explicitly spam/ads.
  - Direct competitor / low-quality SEO farm.
  - Completely unrelated to the topic.
- If a link is helpful, technical, or internal, MARK IT VALID.

STRUCTURE INPUT:
{json.dumps(structure_data)}

LINKS INPUT:
{json.dumps(links_input)}

OUTPUT JSON SCHEMA:
{{
  "structure_suggestions": [
    {{ "id": "unique", "type": "merge", "target_section_ids": [1, 2], "new_heading": "Merged Title", "reason": "Reducing count." }}
  ],
  "link_reviews": [
    {{ "id": 1, "status": "invalid", "reason": "Spam/Competitor" }}
  ]
}}
"""

    try:
        res = generate_with_retry(model, prompt, output_json=True)
        print(f"DEBUG: LLM Response: {res.text[:200]}...")
        
        if not res or not res.text:
            raise ValueError("Empty LLM response")

        clean_json = res.text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_json)

        # Safety & Filtering
        if not isinstance(parsed, dict):
             return {"structure_suggestions": [], "link_reviews": []}
             
        # Filter Link Reviews: Only return INVALID ones to the UI
        # (The user wants to see "suspected defective", not all)
        all_reviews = parsed.get("link_reviews", [])
        filtered_reviews = [r for r in all_reviews if r.get("status") == "invalid"]

        return {
            "structure_suggestions": parsed.get("structure_suggestions", []),
            "link_reviews": filtered_reviews
        }

    except Exception as e:
        print("LLM AUDIT ERROR:", str(e))
        traceback.print_exc()

        return {
            "structure_suggestions": [],
            "link_reviews": []
        }



# =========================
# REWRITE BLOG
# =========================
def rewrite_blog(approved_actions, original_sections, kept_link_urls, link_status_map, page_title="", had_toc=False):
    model = genai.GenerativeModel(MODEL_NAME)

    is_verbatim = not approved_actions or all(a.get("type") == "keep" for a in approved_actions)

    # TOKENIZE LINKS
    tokenized_payload = []

    for s in original_sections:
        soup = BeautifulSoup(s.content, "html.parser")

        for a in soup.find_all("a", href=True):
            token = f"[[LINK:::{a['href']}:::{a.get_text(strip=True)}]]"
            a.replace_with(token)

        tokenized_payload.append({
            "id": s.id,
            "heading": s.heading,
            "html_id": s.html_id,
            "content": str(soup)
        })

    # VERBATIM MODE
    if is_verbatim:
        parts = []
        for item in tokenized_payload:
            hid = f' id="{item["html_id"]}"' if item.get("html_id") else ""
            if item["heading"]:
                parts.append(f"<h2{hid}>{item['heading']}</h2>{item['content']}")
            else:
                parts.append(f"<div{hid}>{item['content']}</div>")
        html_output = "\n\n".join(parts)

    # RESTRUCTURE MODE: Only send sections being merged to LLM
    else:
        # Build a map: section_id -> tokenized payload item
        payload_by_id = {item["id"]: item for item in tokenized_payload}
        
        # Identify which sections are being merged
        merged_section_ids = set()
        merge_groups = []  # List of (target_ids, new_heading)
        for action in approved_actions:
            if action.get("type") == "merge":
                target_ids = action.get("target_section_ids", [])
                new_heading = action.get("new_heading", "Merged Section")
                merged_section_ids.update(target_ids)
                merge_groups.append((target_ids, new_heading))
        
        print(f"REWRITE: {len(merge_groups)} merge groups, {len(merged_section_ids)} sections to merge")
        
        # Process each merge group with a SMALL LLM call
        merged_results = {}  # first_section_id -> merged HTML
        for target_ids, new_heading in merge_groups:
            # Only send the sections being merged
            sections_to_merge = [payload_by_id[sid] for sid in target_ids if sid in payload_by_id]
            
            if not sections_to_merge:
                continue
            
            prompt = f"""You are a blog editor. Merge the following {len(sections_to_merge)} sections into ONE cohesive section.

RULES:
1. Output VALID HTML only. Use <p> for paragraphs, <blockquote> for quotes.
2. Do NOT include any <h2> heading — the system adds it.
3. PRESERVE ALL [[LINK:::url:::text]] tokens EXACTLY as they appear.
4. Combine the content naturally, removing redundancy but keeping all key information.
5. Do NOT add markdown, code fences, or explanations.

NEW HEADING: {new_heading}

SECTIONS TO MERGE:
{json.dumps(sections_to_merge, ensure_ascii=False)}
"""
            print(f"REWRITE: Merging sections {target_ids} -> '{new_heading}' (prompt ~{len(prompt)} chars)")
            res = generate_with_retry(model, prompt)
            merged_html = res.text.replace("```html", "").replace("```", "").strip()
            merged_results[target_ids[0]] = (new_heading, merged_html)
            
            # Small delay between merge calls to avoid rate limits
            time.sleep(2)
        
        # Assemble final HTML: verbatim sections + merged sections
        parts = []
        skip_ids = set()
        for item in tokenized_payload:
            sid = item["id"]
            
            if sid in skip_ids:
                continue
            
            if sid in merged_results:
                # This is the first section in a merge group — insert merged content
                new_heading, merged_html = merged_results[sid]
                new_slug = slugify(new_heading)
                parts.append(f'<h2 id="{new_slug}">{new_heading}</h2>\n{merged_html}')
                # Skip the rest of the sections in this merge group
                for target_ids, _ in merge_groups:
                    if sid == target_ids[0]:
                        skip_ids.update(target_ids[1:])  # Skip subsequent sections
                        break
            else:
                # Keep this section verbatim
                hid = f' id="{item["html_id"]}"' if item.get("html_id") else ""
                if item["heading"]:
                    parts.append(f'<h2{hid}>{item["heading"]}</h2>{item["content"]}')
                else:
                    parts.append(f'<div{hid}>{item["content"]}</div>')
        
        html_output = "\n\n".join(parts)
        print(f"REWRITE: Assembled {len(parts)} sections in final output")

    # DETOKENIZE LINKS
    import re as _re
    token_count = len(_re.findall(r'\[\[LINK:::', html_output))
    print(f"REWRITE: {token_count} link tokens to detokenize")
    print(f"REWRITE: link_status_map has {len(link_status_map)} entries, keep_all={not kept_link_urls}")
    
    normalized_status = {
        normalize_url(u): ("valid" if s in ("valid", "alive") else "invalid")
        for u, s in link_status_map.items()
    }

    # If no kept_link_urls provided, DEFAULT TO KEEPING ALL LINKS
    keep_all = not kept_link_urls
    normalized_kept = {normalize_url(u) for u in kept_link_urls} if kept_link_urls else set()

    def replace_token(match):
        href, text = match.group(1), match.group(2)
        norm_href = normalize_url(href)
        
        # Detect anchor links: #fragment OR full URL with fragment
        is_anchor = href.startswith("#") or bool(urlparse(href).fragment)

        # Determine link status
        status = "valid" if is_anchor else normalized_status.get(norm_href, "valid")

        # STRIP LOGIC: Only strip INVALID links that user did NOT choose to keep
        # Valid/alive links are ALWAYS preserved
        if not keep_all and not is_anchor and status == "invalid" and norm_href not in normalized_kept:
            return text  # Strip this invalid link

        if status == "valid":
            return f'<a href="{href}" class="valid-link">{text}</a>'
        else:
            return f'<span class="invalid-link">{text}</span>'

    final_html = re.sub(r'\[\[LINK:::(.*?):::(.*?)\]\]', replace_token, html_output, flags=re.DOTALL)

    # Debug: check what survived
    valid_count = final_html.count('class="valid-link"')
    invalid_count = final_html.count('class="invalid-link"')
    print(f"REWRITE: After detokenize: {valid_count} valid-link, {invalid_count} invalid-link elements")

    # =========================
    # HEADING + TOC FIX
    # =========================
    soup = BeautifulSoup(final_html, "html.parser")
    # Helper: Check if a URL is an anchor/navigation link (fragment-based)
    def is_anchor_link(href):
        """True if the link points to a fragment on the same or similar page."""
        if not href:
            return False
        if href.startswith('#'):
            return True
        parsed = urlparse(href)
        return bool(parsed.fragment)  # Any URL with a # fragment
    
    # Debug: count lists and anchor links
    all_lists = soup.find_all(['ul', 'ol'])
    print(f"REWRITE DEBUG: Found {len(all_lists)} ul/ol elements in final HTML")
    for i, ul in enumerate(all_lists):
        lis = ul.find_all('li', recursive=False)
        anchor_links = [a for li in lis for a in li.find_all('a', href=True) if is_anchor_link(a.get('href', ''))]
        print(f"  LIST[{i}]: {len(lis)} items, {len(anchor_links)} anchor links, first: {anchor_links[0].get_text()[:30] if anchor_links else 'N/A'}")

    main_level = detect_main_heading_level(str(soup))

    if main_level:
        headings = soup.find_all(main_level)
        used_ids = set()

        for h in headings:
            text = h.get_text(strip=True)
            if not text:
                continue
            new_id = slugify(text)
            base = new_id
            counter = 1
            while new_id in used_ids:
                new_id = f"{base}-{counter}"
                counter += 1
            h["id"] = new_id
            used_ids.add(h["id"])

        # ========================================
        # ALWAYS: Clean up old ToC remnants
        # ========================================
        
        # STEP 1: Remove ToC by known ID/class
        for existing_toc in list(soup.find_all(id="table-of-contents")):
            existing_toc.decompose()
        for existing_toc in list(soup.find_all(class_="toc")):
            existing_toc.decompose()
        for existing_toc in list(soup.find_all(class_="table-of-contents")):
            existing_toc.decompose()
        
        # STEP 2: Remove "Table of Contents" headings + following lists
        for header in list(soup.find_all(['h2', 'h3', 'h4'])):
            if header.get_text(strip=True).lower() in ("table of contents", "toc", "contents"):
                next_elem = header.find_next_sibling()
                if next_elem and next_elem.name in ('ul', 'ol'):
                    next_elem.decompose()
                header.decompose()
        
        # STEP 3: Remove ALL anchor-link lists (old inline ToC)
        for ul in list(soup.find_all(['ul', 'ol'])):
            lis = ul.find_all('li', recursive=False)
            if not lis or len(lis) < 2:
                continue
            
            anchor_count = 0
            for li in lis:
                a_tags = li.find_all('a', href=True)
                for a in a_tags:
                    if is_anchor_link(a.get('href', '')):
                        anchor_count += 1
                        break
            
            if anchor_count >= len(lis) * 0.6:
                print(f"TOC CLEANUP: Removing anchor list ({anchor_count}/{len(lis)} items)")
                ul.decompose()
        
        # STEP 4: Remove loose anchor-link paragraphs before first heading
        first_heading = soup.find(main_level)
        if first_heading:
            for elem in list(first_heading.find_all_previous()):
                if elem.name == 'p':
                    links_in_p = elem.find_all('a', href=True)
                    text = elem.get_text(strip=True)
                    link_text = ''.join(a.get_text(strip=True) for a in links_in_p)
                    if links_in_p and len(link_text) > len(text) * 0.7:
                        if all(is_anchor_link(a.get('href', '')) for a in links_in_p):
                            print(f"TOC CLEANUP: Removing anchor paragraph: {text[:50]}")
                            elem.decompose()

        # ========================================
        # Conditionally build new ToC
        # Only if original had ToC OR >= 5 main headings
        # ========================================
        headings = soup.find_all(main_level)
        should_build_toc = had_toc or len(headings) >= 5
        print(f"TOC: had_toc={had_toc}, heading_count={len(headings)}, building={'YES' if should_build_toc else 'NO'}")
        
        if should_build_toc and headings:
            toc_div = soup.new_tag("div", id="table-of-contents")
            toc_div["style"] = "margin-bottom: 1.5em; padding: 1em; border: 1px solid #e5e7eb; border-radius: 8px; background: #f9fafb;"
            toc_title = soup.new_tag("h3")
            toc_title.string = "Table of Contents"
            toc_title["style"] = "margin-top: 0; margin-bottom: 0.5em;"
            toc_div.append(toc_title)
            
            toc_list = soup.new_tag("ul")
            toc_list["style"] = "list-style: none; padding-left: 0; margin: 0;"
            for h in headings:
                h_text = h.get_text(strip=True)
                if not h_text:
                    continue
                li = soup.new_tag("li")
                li["style"] = "margin-bottom: 0.3em;"
                a_tag = soup.new_tag("a", href=f"#{h['id']}")
                a_tag.string = h_text
                a_tag["style"] = "text-decoration: none; color: #2563eb; font-weight: 500;"
                li.append(a_tag)
                toc_list.append(li)
            toc_div.append(toc_list)
            
            # Insert just before the first main heading
            first_h = headings[0]
            first_h.insert_before(toc_div)
            
            print(f"TOC: Built new ToC with {len(headings)} entries")

    # ENFORCE MAX 6 H2
    h2s = soup.find_all("h2")
    if len(h2s) > 6:
        for i in range(6, len(h2s)):
            h2s[i].name = "h3"

    # Prepend page title as h1
    final_output = str(soup)
    if page_title:
        title_slug = slugify(page_title)
        title_html = f'<h1 id="{title_slug}">{page_title}</h1>\n'
        final_output = title_html + final_output
    
    return final_output

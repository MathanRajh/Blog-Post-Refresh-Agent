import google.generativeai as genai
import os
import json
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse
import re

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL_NAME = "gemini-2.0-flash"


# =========================
# SAFE GENERATE (NO TOKEN WASTE)
# =========================
def generate_with_retry(model, prompt, output_json=False):
    max_retries = 5
    base_delay = 2.0

    for attempt in range(max_retries):
        try:
            config = {"response_mime_type": "application/json"} if output_json else {}

            return model.generate_content(prompt, generation_config=config)

        except Exception as e:
            msg = str(e).lower()

            if "429" in msg or "resource exhausted" in msg:
                time.sleep(base_delay * (2 ** attempt))
                continue

            if any(x in msg for x in ["internal", "unavailable", "timeout"]):
                time.sleep(base_delay * (attempt + 1))
                continue

            raise

    raise RuntimeError("Gemini failed after retries")


# =========================
# AUDIT BLOG (MINIMAL TOKENS)
# =========================
def audit_blog_logic(db_sections, db_links):
    model = genai.GenerativeModel(MODEL_NAME)

    structure_data = [
        {"id": s.id, "heading": s.heading}
        for s in db_sections
    ]

    links_data = [
        {
            "id": l.id,
            "anchor": (l.text or "")[:80],
            "url": l.url
        }
        for l in db_links
    ]

    # OPTIMIZATION: If sections <= 6, DO NOT ask LLM for structure changes.
    # We only prompt for link reviews in this case.
    structure_instruction = ""
    if len(db_sections) <= 6:
        structure_instruction = "NOTE: Current structure has <= 6 sections. DO NOT propose any structure changes. Return empty list []."
    else:
        structure_instruction = f"NOTE: Current input has {len(db_sections)} sections. You MUST reduce this to 6 or fewer. Propose merging {len(db_sections) - 6} or more sections."

    prompt = f"""
You are a strict blog auditor and editor.

GOAL 1: STRUCTURE OPTIMIZATION (CRITICAL - HARD LIMIT)
- The final blog post MUST have a MAXIMUM of 6 main sections (h2).
{structure_instruction}
- If > 6 sections remain, you have FAILED.
- **Micro-Section Merging**: Combine short or semantically related sections.
- **Preserve Info**: Do NOT remove valid content sections just to save space. MERGE them instead.
- **Semantic Grouping**: Group sections that discuss the same sub-topic.

GOAL 2: LINK EVALUATION
- Review every link's anchor text and URL.
- Status: "valid" OR "invalid".
- INVALID LINKS:
  - Broken or dead links (404, etc.)
  - Spam, gambling, adult content
  - Excessive affiliate links without value
  - Irrelevant to the topic
- VALID LINKS:
  - Helpful external resources
  - Relevant internal links
  - Source citations

STRUCTURE INPUT:
{json.dumps(structure_data, ensure_ascii=False)}

LINKS INPUT:
{json.dumps(links_data, ensure_ascii=False)}

OUTPUT JSON ONLY:
{{
  "structure_suggestions": [
    {{
       "id": "unique_id",
       "type": "merge",  // PRIORITIZE MERGING OVER REMOVING
       "target_section_ids": [1, 2, 3], // IDs of sections to merge into one
       "new_heading": "Unified Topic Heading",
       "reason": "Merging related sections X, Y, Z to reduce count while keeping info."
    }}
  ],
  "link_reviews": [
    {{
      "id": 1, // Link ID
      "status": "valid | invalid",
      "reason": "Link is broken (404) | Link is purely spam"
    }}
  ]
}}
"""

    try:
        res = generate_with_retry(model, prompt, output_json=True)
        return json.loads(res.text)
    except Exception:
        return {"structure_suggestions": [], "link_reviews": []}


# =========================
# URL NORMALIZER (SINGLE SOURCE)
# =========================
def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        u = clean.lower().replace("https://", "").replace("http://", "").replace("www.", "")
        return u.rstrip("/")
    except Exception:
        return url.lower().strip()


# =========================
# REWRITE BLOG (NO EXTRA TOKENS)
# =========================
import re # Added for regex

# ... (imports)

# ... (audit_blog_logic)

# ... (normalize_url)

# =========================
# REWRITE BLOG (TOKEN STRATEGY)
# =========================
def rewrite_blog(approved_actions, original_sections, kept_link_urls, link_status_map):
    model = genai.GenerativeModel(MODEL_NAME)

    # 1. TOKENIZE LINKS (PRE-PROCESSING)
    # We convert <a href="u">text</a> into [[LINK:::u:::text]]
    # This protects them from being stripped by the LLM.
    
    tokenized_payload = []
    
    for s in original_sections:
        soup = BeautifulSoup(s.content, "html.parser")
        
        # Replace all links with tokens
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            # Create token (simple escaping for safety)
            token = f"[[LINK:::{href}:::{text}]]"
            a.replace_with(token)
            
        tokenized_payload.append({
            "id": s.id,
            "heading": s.heading,
            "content": str(soup) # Now contains tokens instead of tags
        })

    # 2. PROMPT GENERATION
    print(f"DEBUG: Tokenized Content Sample: {json.dumps(tokenized_payload)[:500]}")

    # DETECT IF WE ARE MERGING OR JUST KEEPING
    # If all actions are "keep" or empty, we want VERBATIM copy.
    is_restructuring = any(a.get("type") in ["merge", "remove"] for a in approved_actions)
    
    mode_instruction = ""
    if is_restructuring:
        mode_instruction = "Task: RESTRUCTURE & MERGE. When merging sections, combine their content logically. DO NOT summarize to the point of losing details. KEEP ALL KEY INFO. Ensure Final Output has MAX 6 <h2> headings."
    else:
        mode_instruction = "Task: ASSEMBLE ONLY. COPY CONTENT EXACTLY VERBATIM. DO NOT REWRITE OR SUMMARIZE."

    prompt = f"""
You are an expert HTML assembler.

{mode_instruction}

INPUT CONTENT:
{json.dumps(tokenized_payload, ensure_ascii=False)}

APPROVED ACTIONS:
{json.dumps(approved_actions, ensure_ascii=False)}

CRITICAL RULES:
1. **PRESERVE TOKENS**: The input contains tokens like `[[LINK:::url:::text]]`. You MUST preserve these EXACTLY as they are.
2. **SEMANTIC MERGING**: If merging, combine the text of the sections smoothly.
3. **MAX 6 SECTIONS**: The final output MUST NOT have more than 6 `<h2>` headings. If you have more, merge them.
4. **FORMATTING**: Return properly formatted HTML body content (h2, p, ul).
5. **NO MARKDOWN**: Return raw HTML only. DO NOT wrap in ```html``` code blocks.
6. **NO METADATA**: Do NOT include `<html>`, `<head>`, `<title>`, `<meta>`, or `<body>` tags. Return ONLY the inner content of the body.
7. **NO CONVERSATIONAL FILLER**: Do NOT say "Here is the HTML", "I have merged...", etc. Just return the code.

RETURN PURE HTML CONTENT ONLY.
"""

    res = generate_with_retry(model, prompt)
    html_output = res.text.replace("```html", "").replace("```", "").strip()
    
    # POST-PROCESS: Remove common conversational prefixes/suffixes if LLM ignores rules
    html_output = re.sub(r'^Here is the.*?<', '<', html_output, flags=re.DOTALL | re.IGNORECASE)
    html_output = re.sub(r'Since you requested.*?<', '<', html_output, flags=re.DOTALL | re.IGNORECASE)
    
    # POST-PROCESS: Remove <head> logic if it sneaks in
    html_output = re.sub(r'<head>.*?</head>', '', html_output, flags=re.DOTALL | re.IGNORECASE)
    html_output = re.sub(r'<!DOCTYPE.*?>', '', html_output, flags=re.DOTALL | re.IGNORECASE)
    html_output = re.sub(r'<html>', '', html_output, flags=re.IGNORECASE)
    html_output = re.sub(r'</html>', '', html_output, flags=re.IGNORECASE)
    html_output = re.sub(r'<body>', '', html_output, flags=re.IGNORECASE)
    html_output = re.sub(r'</body>', '', html_output, flags=re.IGNORECASE)
    html_output = html_output.strip()
    
    # POST-PROCESS: Fix unicode escapes (e.g., \u2019 -> ’)
    try:
        if "\\u" in html_output:
            html_output = html_output.encode('utf-8').decode('unicode_escape')
    except Exception:
        pass 

    # SAFETY NET: Enforce Max 6 <h2>
    # If the LLM failed and returned > 6 h2s, we downgrade the extras to h3.
    try:
        safety_soup = BeautifulSoup(html_output, "html.parser")
        h2s = safety_soup.find_all('h2')
        if len(h2s) > 6:
            print(f"DEBUG: Found {len(h2s)} h2 tags. Downgrading {len(h2s)-6} excess tags to h3.")
            for i in range(6, len(h2s)):
                h2s[i].name = 'h3'
            html_output = str(safety_soup)
    except Exception as e:
        print(f"DEBUG: Safety net failed: {e}")

    print(f"DEBUG: LLM Output Sample: {html_output[:500]}")

    # 3. DETOKENIZE & STYLE (POST-PROCESSING)
    # Restore tokens back to <a> tags with appropriate styles
    
    normalized_status = {
        normalize_url(u): ("valid" if s == "valid" else "invalid")
        for u, s in link_status_map.items()
    }
    normalized_kept = {normalize_url(u) for u in kept_link_urls}

    # Track which URLs we have restored to avoid double-linking in fallback
    restored_urls = set()

    def replace_token(match):
        href = match.group(1)
        text = match.group(2)
        
        norm_href = normalize_url(href)
        restored_urls.add(norm_href)
        
        # Check if user kept this link
        if norm_href not in normalized_kept:
             return text # Return just text if link was rejected

        # Determine Style
        status = normalized_status.get(norm_href, "invalid")
        
        style = ""
        title = ""
        
        if status == "valid":
             style = "color: #16a34a !important; font-weight: 700 !important; text-decoration: underline !important; background-color: #dcfce7 !important; padding: 2px 4px; border-radius: 4px;"
             title = "✅ Valid link"
        else:
             style = "color: #dc2626 !important; font-weight: 700 !important; text-decoration: line-through !important; background-color: #fee2e2 !important; padding: 2px 4px; border-radius: 4px;"
             title = "❌ Invalid link"
             
        return f'<a href="{href}" style="{style}" title="{title}">{text}</a>'

    # Regex to find [[LINK:::url:::text]] and replace using function
    # Using DOTALL to handle multi-line tokens if LLM adds newlines
    final_html = re.sub(r'\[\[LINK:::(.*?):::(.*?)\]\]', replace_token, html_output, flags=re.DOTALL)

    # 4. FALLBACK: TEXT MATCHING
    # If LLM stripped tokens, try to find the anchor text in the HTML and re-link it.
    # This is a safety net.
    soup = BeautifulSoup(final_html, "html.parser")
    
    # Get all original links that haven't been restored yet
    missing_links = []
    for s in original_sections:
        origin_soup = BeautifulSoup(s.content, "html.parser")
        for a in origin_soup.find_all("a", href=True):
            if normalize_url(a['href']) not in restored_urls:
                missing_links.append({
                    "href": a['href'],
                    "text": a.get_text(strip=True),
                    "norm": normalize_url(a['href'])
                })
    
    if missing_links:
        print(f"DEBUG: Found {len(missing_links)} missing links. Attempting fallback text match.")
        
        # Iterate over text nodes only to avoid breaking HTML tags
        for text_node in soup.find_all(string=True):
            if not text_node.strip(): continue
            
            new_text = str(text_node)
            modified = False
            
            for link in missing_links:
                if link['norm'] not in normalized_kept: continue
                
                # Simple case-insensitive string replace for the anchor text
                # We use word boundary \b to avoid partial replacements if possible, 
                # but anchor text might contain spaces.
                # Let's try direct replacement of the phrase.
                anchor = link['text']
                if not anchor or len(anchor) < 3: continue # Skip too short matches to avoid false positives
                
                if anchor in new_text:
                    status = normalized_status.get(link['norm'], "invalid")
                    if status == "valid":
                        style = "color: #16a34a !important; font-weight: 700 !important; text-decoration: underline !important; background-color: #dcfce7 !important; padding: 2px 4px; border-radius: 4px;"
                        title = "✅ Valid link (Restored)"
                    else:
                        style = "color: #dc2626 !important; font-weight: 700 !important; text-decoration: line-through !important; background-color: #fee2e2 !important; padding: 2px 4px; border-radius: 4px;"
                        title = "❌ Invalid link (Restored)"
                    
                    replacement = f'<a href="{link["href"]}" style="{style}" title="{title}">{anchor}</a>'
                    new_text = new_text.replace(anchor, replacement)
                    modified = True
                    # Mark as restored so we don't double replace
                    restored_urls.add(link['norm'])
            
            if modified:
                text_node.replace_with(BeautifulSoup(new_text, "html.parser"))

    return str(soup)
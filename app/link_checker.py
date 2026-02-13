import requests
from bs4 import BeautifulSoup
import concurrent.futures

def check_single_link(link_obj):
    """
    Takes a Link model object (or dict), validates it, fetches title.
    """
    url = link_obj['url']
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    result = {
        "url": url,
        "section_id": link_obj.get("section_id"),
        "text": link_obj.get("text", "Link"),  # Preserve original anchor text
        "status": "pending",
        "target_title": None,
        "reason": None
    }

    try:
        # 0. Internal Anchors (Skip Network Check)
        if url.startswith('#'):
            result['status'] = 'valid'
            result['target_title'] = "Internal Anchor"
            return result
        
        # 0b. Strip fragment from URL for HTTP validation
        # Servers never see the fragment (#section) — it's client-side only
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        if parsed.fragment:
            # Validate the URL WITHOUT the fragment
            url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ''))
            result['target_title'] = f"Fragment: #{parsed.fragment}"

        # 1. Try HEAD Request first (Fast)
        try:
            resp = requests.head(url, headers=headers, timeout=15, allow_redirects=True)
            
            # If Method Not Allowed (405) or Not Acceptable (406), try GET
            if resp.status_code in (405, 406):
                resp = requests.get(url, headers=headers, timeout=15, stream=True)
                resp.close()
            
            # Special Handling for 403 (Forbidden) - Likely Anti-Bot.
            # We assume the link is VALID/ALIVE if we get a 403, rather than failing it.
            if resp.status_code == 403:
                result['status'] = 'alive'
                result['target_title'] = "Protected Content (403)"
                return result
                
        except requests.exceptions.RequestException:
            # Fallback to GET if HEAD failed completely
            resp = requests.get(url, headers=headers, timeout=15, stream=True)
            resp.close()
            
            # Check 403 again after GET fallback
            if resp.status_code == 403:
                result['status'] = 'alive'
                result['target_title'] = "Protected Content (403)"
                return result

        if resp.status_code >= 400:
            result['status'] = 'invalid'
            result['reason'] = f"Broken Link ({resp.status_code})"
            return result

        # 2. GET Request (For Title - Context) IF we need it and haven't fully fetched it
        # We only fetch title if it's likely HTML
        if 'text/html' in resp.headers.get('Content-Type', ''):
            try:
                with requests.get(url, headers=headers, timeout=15, stream=True) as r:
                    # Read first 8KB only for title
                    chunk = next(r.iter_content(8192), b'').decode('utf-8', errors='ignore')
                    soup = BeautifulSoup(chunk, 'html.parser')
                    result['target_title'] = soup.title.string.strip()[:100] if soup.title and soup.title.string else "No Title"
            except Exception:
                pass # Title fetch failure shouldn't fail the link status
        
        result['status'] = 'alive' # Ready for LLM to judge relevance
        return result

    except requests.exceptions.Timeout:
        result['status'] = 'invalid'
        result['reason'] = "Timeout"
        return result
    except requests.exceptions.ConnectionError:
        result['status'] = 'invalid'
        result['reason'] = "Connection Error"
        return result
    except Exception as e:
        result['status'] = 'invalid'
        result['reason'] = f"Error: {str(e)}"
        return result

def batch_validate_links(links_list):
    """
    Runs parallel validation on a list of link dictionaries.
    """
    validated = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_link = {executor.submit(check_single_link, l): l for l in links_list}
        for future in concurrent.futures.as_completed(future_to_link):
            validated.append(future.result())
    return validated
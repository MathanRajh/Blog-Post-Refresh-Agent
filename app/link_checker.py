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
        "section_id": link_obj.get("section_id"), # Pass through ID
        "status": "pending",
        "target_title": None,
        "reason": None
    }

    try:
        # 1. Try HEAD Request first (Fast)
        try:
            resp = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
            
            # If Method Not Allowed (405) or Forbidden (403), try GET
            if resp.status_code in [405, 403]:
                resp = requests.get(url, headers=headers, timeout=5, stream=True)
                resp.close() # Close immediately, just checking status
            
        except requests.exceptions.RequestException:
            # Fallback to GET if HEAD failed completely
            resp = requests.get(url, headers=headers, timeout=5, stream=True)
            resp.close()

        if resp.status_code >= 400:
            result['status'] = 'invalid'
            result['reason'] = f"Broken Link ({resp.status_code})"
            return result

        # 2. GET Request (For Title - Context) IF we need it and haven't fully fetched it
        # We only fetch title if it's likely HTML
        if 'text/html' in resp.headers.get('Content-Type', ''):
            try:
                with requests.get(url, headers=headers, timeout=5, stream=True) as r:
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
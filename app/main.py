from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from urllib.parse import urlparse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import traceback

from app.database import get_db, reset_db
from app.models import Site, Section, Link
from app.scraper import scrape_url_structured
from app.link_checker import batch_validate_links
from app.llm import audit_blog_logic, rewrite_blog

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

class UrlRequest(BaseModel):
    url: str

class GenerateRequest(BaseModel):
    accepted_suggestion_ids: Optional[List[str]] = None
    all_suggestions: Optional[List[Dict[str, Any]]] = None
    kept_link_urls: Optional[List[str]] = None


# =========================
# ANALYZE
# =========================
@app.post("/analyze")
async def analyze(req: UrlRequest, db: Session = Depends(get_db)):
    reset_db()

    try:
        scraped_data = scrape_url_structured(req.url)
        
        # Extract metadata (first item) and actual sections
        metadata = scraped_data[0] if scraped_data and scraped_data[0].get("_metadata") else {}
        section_data = [s for s in scraped_data if not s.get("_metadata")]
        page_title = metadata.get("page_title", "")
        
        print(f"PIPELINE: Scraped {len(section_data)} sections, title: '{page_title}'")

        site = Site(url=req.url, domain=urlparse(req.url).netloc, page_title=page_title, had_toc=metadata.get("had_toc", False))
        db.add(site)
        db.commit()
        db.refresh(site)

        all_raw_links = []

        for idx, sec in enumerate(section_data):
            section = Section(
                site_id=site.id,
                heading=sec["heading"],
                content=sec["content"],
                level=sec.get("level", "h2"),
                html_id=sec.get("id_attr"),
                order_index=idx
            )
            db.add(section)
            db.commit()
            db.refresh(section)

            for l in sec.get("links", []):
                l["section_id"] = section.id
                all_raw_links.append(l)

        # Separate internal vs external links BEFORE validation
        source_domain = urlparse(req.url).netloc
        external_links = []
        internal_results = []
        
        for l in all_raw_links:
            link_url = l.get("url", "")
            parsed_link = urlparse(link_url)
            
            # Internal: anchor links, no domain, or same domain
            if not link_url or link_url.startswith('#') or not parsed_link.netloc or parsed_link.netloc == source_domain:
                internal_results.append({
                    "section_id": l["section_id"],
                    "url": link_url,
                    "text": l.get("text", "Link"),
                    "target_title": "Internal Link",
                    "status": "valid",
                    "reason": None
                })
            else:
                external_links.append(l)
        
        print(f"PIPELINE: {len(internal_results)} internal links (auto-valid), {len(external_links)} external links to validate")
        
        # Only validate external links via HTTP
        validated = batch_validate_links(external_links)
        
        # Combine results
        all_validated = internal_results + validated

        for v in all_validated:
            db.add(Link(
                section_id=v["section_id"],
                url=v["url"],
                text=v.get("text", "Link"),
                target_title=v.get("target_title"),
                status=v["status"],
                reason=v.get("reason")
            ))
        db.commit()

        # Count validation results
        alive_count = sum(1 for v in all_validated if v['status'] == 'alive')
        valid_count = sum(1 for v in all_validated if v['status'] == 'valid')
        invalid_count = sum(1 for v in all_validated if v['status'] == 'invalid')
        print(f"PIPELINE: After validation: alive={alive_count}, valid={valid_count}, invalid={invalid_count}")
        for v in validated[:5]:
            print(f"  VALIDATED: {v.get('url', '?')[:60]} -> {v.get('status')} ({v.get('reason', 'N/A')})")

        sections = db.query(Section).order_by(Section.order_index).all()
        links = db.query(Link).all()

        # Separate EXTERNAL links from internal anchors
        # Internal = starts with # or is same-domain with fragment
        source_domain = urlparse(req.url).netloc
        
        def is_external_link(link_url):
            """True only for genuine external links (different domain)."""
            if not link_url or link_url.startswith('#'):
                return False
            parsed = urlparse(link_url)
            # No domain = relative/anchor = internal
            if not parsed.netloc:
                return False
            # Same domain = internal (cross-post links don't need validation)
            if parsed.netloc == source_domain:
                return False
            return True

        # Only send EXTERNAL alive links to LLM for relevance audit
        external_alive = [l for l in links if l.status == "alive" and is_external_link(l.url)]
        print(f"PIPELINE: External alive links for LLM audit: {len(external_alive)}")
        print(f"PIPELINE: DB Sections for LLM: {len(sections)} -> levels: {[s.level for s in sections]}")
        
        audit = audit_blog_logic(sections, external_alive)
        reviews = audit.get("link_reviews", [])

        # Hydrate DB with LLM Relevance checks
        for r in reviews:
            link = next((l for l in external_alive if l.id == r.get("id")), None)
            if link:
                link.status = "valid" if r.get("status") == "valid" else "invalid"
                link.reason = r.get("reason")
                r["url"] = link.url

        # Upgrade remaining 'alive' external links to 'valid'
        for l in external_alive:
            if l.status == "alive":
                l.status = "valid"
        
        # Also mark all internal anchors as 'valid' (they're navigation, not broken)
        for l in links:
            if l.status == "alive" and not is_external_link(l.url):
                l.status = "valid"

        db.commit()

        # Build link report for frontend: ONLY external broken links
        all_link_reports = list(reviews)
        
        reviewed_urls = {r.get("url") for r in reviews if r.get("url")}
        broken_links = db.query(Link).filter(Link.status == "invalid").all()
        for bl in broken_links:
            if bl.url not in reviewed_urls and is_external_link(bl.url):
                all_link_reports.append({
                    "id": bl.id,
                    "url": bl.url,
                    "status": "invalid",
                    "reason": bl.reason or "Broken Link"
                })
        
        print(f"PIPELINE: Returning {len(audit.get('structure_suggestions', []))} structure suggestions, {len(all_link_reports)} link reports")

        return {
            "audit": {
                "structure_suggestions": audit.get("structure_suggestions", []),
                "link_reviews": all_link_reports
            }
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"ERROR DETAILS: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================
# GENERATE
# =========================
@app.post("/generate")
async def generate(req: GenerateRequest, db: Session = Depends(get_db)):
    try:
        accepted = set(req.accepted_suggestion_ids or [])
        all_suggestions = req.all_suggestions or []

        approved_actions = [
            a for a in all_suggestions
            if a.get("id") in accepted
        ]

        sections = db.query(Section).order_by(Section.order_index).all()
        links = db.query(Link).all()

        link_status_map = {l.url: l.status for l in links}

        print("GENERATE: Short cooldown before Gemini call...")
        import time as _time
        _time.sleep(5)

        # Get page title and toc flag from Site record
        site = db.query(Site).first()
        page_title = site.page_title if site else ""
        had_toc = site.had_toc if site else False

        html = rewrite_blog(
            approved_actions,
            sections,
            req.kept_link_urls or [],
            link_status_map,
            page_title=page_title,
            had_toc=had_toc
        )

        return {"html": html}

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"GENERATE ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)

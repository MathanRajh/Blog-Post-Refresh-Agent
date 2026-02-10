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

        site = Site(url=req.url, domain=urlparse(req.url).netloc)
        db.add(site)
        db.commit()
        db.refresh(site)

        all_raw_links = []

        for idx, sec in enumerate(scraped_data):
            section = Section(
                site_id=site.id,
                heading=sec["heading"],
                content=sec["content"],
                level=sec.get("level", "h2"),
                order_index=idx
            )
            db.add(section)
            db.commit()
            db.refresh(section)

            for l in sec.get("links", []):
                l["section_id"] = section.id
                all_raw_links.append(l)

        validated = batch_validate_links(all_raw_links)

        for v in validated:
            db.add(Link(
                section_id=v["section_id"],
                url=v["url"],
                text=v.get("text", "Link"),
                target_title=v.get("target_title"),
                status=v["status"],
                reason=v.get("reason")
            ))
        db.commit()

        sections = db.query(Section).order_by(Section.order_index).all()
        links = db.query(Link).all()

        audit = audit_blog_logic(sections, links)
        reviews = audit.get("link_reviews", [])

        # Hydrate DB
        for r in reviews:
            link = next((l for l in links if l.id == r.get("id")), None)
            if link:
                link.status = "valid" if r.get("status") == "valid" else "invalid"
                r["url"] = link.url

        db.commit()

        return {
            "audit": {
                "structure_suggestions": audit.get("structure_suggestions", []),
                "link_reviews": reviews
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

        html = rewrite_blog(
            approved_actions,
            sections,
            req.kept_link_urls or [],
            link_status_map
        )

        return {"html": html}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)


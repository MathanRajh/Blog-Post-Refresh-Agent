# Blog Audit & Rewrite Engine (RAG + LLM)

A production-ready backend service that:
- Scrapes a blog post
- Audits structure (max 6 main sections)
- Validates and classifies links
- Uses an LLM to suggest **safe structural merges**
- Rewrites HTML while **preserving links exactly**
- Gives full user control before any change is applied

Built with **FastAPI, PostgreSQL, Gemini LLM**, and **Selenium**.

---

## ✨ Features

- 🔍 **Structured Blog Scraping** (Selenium + BeautifulSoup)
- 🧠 **LLM-based Structure & Link Auditing**
- 🔗 **Parallel Link Validation** (HEAD → GET fallback)
- 🧩 **Safe Section Merging** (no silent deletions)
- 🛡️ **Link Tokenization** (prevents LLM from breaking `<a>` tags)
- 🧪 **Deterministic Rewrites** (verbatim unless user approves)
- ⚡ **Low-token strategy** (optimized prompts + retries)

---

## 🧰 Tech Stack

- **Backend**: FastAPI
- **Database**: PostgreSQL (SQLAlchemy ORM)
- **LLM**: Google Gemini (`gemini-2.0-flash`)
- **Scraping**: Selenium (Chrome Headless)
- **Concurrency**: ThreadPoolExecutor

---




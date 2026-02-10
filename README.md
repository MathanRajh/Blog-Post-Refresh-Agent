# Blog Audit & Rewrite Backend

This document explains how to run the backend service locally.

---

## Prerequisites

- Python 3.9+
- Google Chrome (required for Selenium)
- PostgreSQL (running locally)
- Git

---

## Clone the Repository

git clone https://github.com/MathanRajh/Blog-Post-Refresh-Agent.git

Create Virtual Environment:
python -m venv venv
Activate the environment
Linux / macOS

source venv/bin/activate
Windows
venv\Scripts\activate

Install Dependencies:
pip install -r requirements.txt

Environment Variables:
replace the.env with valid key

Notes
The PostgreSQL database must already exist

Tables are created automatically on startup

Run the Server
The application runs on port 8080.

uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload (make sure the backend port is 8080)


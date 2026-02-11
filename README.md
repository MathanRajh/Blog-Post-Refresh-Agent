## Prerequisites
- Python 3.10+
- Chrome/Chromium (for Selenium scraping)

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/MathanRajh/Blog-Post-Refresh-Agent.git
    cd Blog-Post-Refresh-Agent
    ```

2.  **Create and activate a virtual environment (optional but recommended):**
    ```bash
    python -m venv venv
    # Windows
    venv\Scripts\activate
    # macOS/Linux
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configuration:**
    Create a `.env` file in the `backend` directory with the following variable:
    ```env
    GOOGLE_API_KEY=your_google_gemini_api_key
    DATABASE_URL = postgresql url
    ```

## Running the Server

Start the FastAPI server using Uvicorn:

```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.
API documentation (Swagger UI) is available at `http://localhost:8000/docs`.

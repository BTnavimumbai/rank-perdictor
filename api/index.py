import os
import json
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# 1. CORS Configuration: Allows your Hostinger site to talk to Vercel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # For strict security, use ["https://btnavimumbai.com"]
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Data Model
class ResponseSheet(BaseModel):
    url: str
    phone: str

# 3. Google Sheets Authentication Helper
def get_gsheet_client():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS') # Reads the secret you added
    if not creds_json:
        return None
    creds_dict = json.loads(creds_json)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

@app.get("/")
async def health():
    return {"status": "Backend is Live"}

@app.post("/calculate")
async def calculate_marks(data: ResponseSheet):
    try:
        # --- SCRAPING LOGIC ---
        response = requests.get(data.url)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Could not fetch the URL")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # --- EXAMPLE CALCULATION (Add your specific scraping logic here) ---
        # This is where your logic to find marks in the HTML goes
        total_score = 150 
        exam_shift = "21 Jan Morning"

        # --- SAVE TO GOOGLE SHEETS ---
        client = get_gsheet_client()
        if client:
            # Ensure the sheet name "JEE_Predictor" matches your actual Google Sheet
            sheet = client.open("JEE_Predictor").sheet1
            sheet.append_row([data.phone, data.url, total_score, exam_shift])

        return {
            "status": "success",
            "total": total_score,
            "shift": exam_shift
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

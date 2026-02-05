import os
import json
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# Enable CORS so your Hostinger site can talk to Vercel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, replace "*" with "https://btnavimumbai.com"
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GOOGLE SHEETS AUTH CONFIG ---
def get_gspread_client():
    try:
        # Pulls the JSON string from Vercel Environment Variables
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS not found in environment")
            
        creds_dict = json.loads(creds_json)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Auth Error: {e}")
        return None

@app.get("/")
async def health_check():
    return {"status": "Vercel Backend is Live"}

@app.post("/calculate")
async def calculate(request: Request):
    try:
        data = await request.json()
        response_url = data.get("url")
        phone = data.get("phone")

        if not response_url:
            raise HTTPException(status_code=400, detail="URL is required")

        # 1. SCRAPING LOGIC
        # Fetch the NTA Response Sheet HTML
        response = requests.get(response_url)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # --- ADD YOUR SPECIFIC JEE MARK CALCULATION LOGIC HERE ---
        # Example: total_marks = calculate_from_soup(soup)
        total_marks = 150 # Placeholder
        exam_shift = "27 Jan Shift 1" # Placeholder
        
        # 2. SAVE TO GOOGLE SHEETS
        client = get_gspread_client()
        if client:
            sheet = client.open("JEE_Predictor_Data").sheet1
            sheet.append_row([phone, response_url, total_marks, exam_shift])

        return {
            "status": "success",
            "marks": total_marks,
            "shift": exam_shift
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

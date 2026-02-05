import os
import json
import re
import io
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# Enable CORS for your Hostinger site
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

class StudentInput(BaseModel):
    url: str
    phone: str
    percentile: str 
    rank: str

# --- AUTHENTICATION ---
def get_gs_client():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    creds_dict = json.loads(creds_json)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

# --- CANDIDATE INFO EXTRACTION ---
def extract_candidate_info(soup):
    info = {
        "name": "N/A",
        "app_no": "N/A",
        "roll_no": "N/A",
        "test_date": "N/A",
        "test_time": "N/A"
    }
    tables = soup.find_all('table')
    for table in tables:
        text = table.get_text()
        if "Application No" in text:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    label = cols[0].get_text(strip=True)
                    value = cols[1].get_text(strip=True)
                    if "Candidate Name" in label: info["name"] = value
                    elif "Application No" in label: info["app_no"] = value
                    elif "Roll No" in label: info["roll_no"] = value
                    elif "Test Date" in label: info["test_date"] = value
                    elif "Test Time" in label: info["test_time"] = value
            break
    return info

# --- SCORING LOGIC ---
def calculate_marks(q_id, student_res, q_type, ans_key):
    q_id_str = str(q_id).strip()
    student_val = str(student_res).strip()

    if q_id_str == "444792191": return 4 
    if q_id_str in ans_key:
        correct_val = str(ans_key[q_id_str]).strip()
        if "dropped" in correct_val.lower(): return 4

    if q_id_str == "444792493":
        correct_options = ["4447921684", "4447921686", "4447921687"]
        if student_val in correct_options: return 4
        return 0 if student_val in ["Not Answered", "--"] else -1

    if student_val in ["Not Answered", "--"]: return 0
    if q_id_str not in ans_key: return 0 
    
    correct_val = str(ans_key[q_id_str]).strip()
    return 4 if student_val == correct_val else -1

# --- EXTRACTION HELPERS ---
def extract_data_from_chunks(chunks, ans_key):
    rows = []
    for chunk in chunks:
        q_id_match = re.search(r"Question ID\s*[:]\s*(\d+)", chunk)
        if not q_id_match: continue
        q_id = q_id_match.group(1)
        student_res = "Not Answered"
        is_mcq = "Option 1 ID" in chunk
        q_type = "MCQ" if is_mcq else "SA"
        if is_mcq:
            chosen = re.search(r"Chosen Option\s*[:]\s*([1-4])", chunk)
            if chosen:
                opt_num = chosen.group(1)
                opt_match = re.search(rf"Option {opt_num} ID\s*[:]\s*(\d+)", chunk)
                if opt_match: student_res = opt_match.group(1)
        else:
            given_match = re.search(r"Given(?:\s*Answer)?\s*[:]?\s*([-+]?\d*\.?\d+)", chunk)
            if given_match: student_res = given_match.group(1)
        
        marks = calculate_marks(q_id, student_res, q_type, ans_key)
        rows.append([q_id, q_type, student_res, marks])
    return rows

@app.get("/")
async def health():
    return {"status": "Live"}

@app.post("/calculate")
async def process_student(data: StudentInput):
    try:
        client = get_gs_client()
        spreadsheet = client.open("JEE_Predictor_Data")
        
        # 1. Load Answer Key
        ans_tab = spreadsheet.worksheet("ANS")
        ans_key = {str(row['Question ID']): str(row['Correct Response ID']) for row in ans_tab.get_all_records()}

        # 2. Fetch Response Sheet
        link = data.url
        if link.startswith('cdn3'): link = 'https://' + link
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(link, timeout=15, headers=headers)
        
        # 3. Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        cand = extract_candidate_info(soup)
        
        content = soup.get_text(separator=' ', strip=True)
        chunks = re.split(r"(?=Q\.\d+)", content)
        report_data = extract_data_from_chunks(chunks, ans_key)

        if not report_data:
            return {"status": "error", "message": "Could not parse questions"}

        # 4. Calculate Scores
        math_score = sum(item[3] for item in report_data[0:25])
        phy_score = sum(item[3] for item in report_data[25:50])
        chem_score = sum(item[3] for item in report_data[50:75])
        total_score = math_score + phy_score + chem_score

        # 5. Update Individual Tab (Fixed Syntax)
        sheet_name = str(data.phone)
        try:
            ws = spreadsheet.worksheet(sheet_name)
            ws.clear()
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="5")
        
        # NEW GSPREAD SYNTAX: ws.update([data]) instead of ws.update(range, data)
        ws.update([["Question ID", "Type", "Response", "Marks"]] + report_data)

        # 6. Update Master Sheet (World Class 13-Column Order)
        master = spreadsheet.sheet1
        master.append_row([
            data.phone,         # A
            cand["name"],       # B
            cand["app_no"],     # C
            cand["roll_no"],    # D
            cand["test_date"],   # E
            cand["test_time"],   # F
            phy_score,          # G
            chem_score,         # H
            math_score,         # I
            total_score,        # J
            data.percentile,    # K
            data.rank,          # L
            data.url            # M
        ])

        return {
            "status": "success",
            "name": cand["name"],
            "total": total_score,
            "phy": phy_score,
            "chem": chem_score,
            "math": math_score,
            "percentile": data.percentile,
            "rank": data.rank
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

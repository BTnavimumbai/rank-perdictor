import os
import json
import re
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

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

def get_gs_client():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    creds_dict = json.loads(creds_json)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def extract_candidate_info(soup):
    info = {"name": "N/A", "app_no": "N/A", "roll_no": "N/A", "test_date": "N/A", "test_time": "N/A"}
    tables = soup.find_all('table')
    for table in tables:
        if "Application No" in table.get_text():
            for row in table.find_all('tr'):
                cols = row.find_all('td')
                if len(cols) >= 2:
                    label = cols[0].get_text(strip=True)
                    val = cols[1].get_text(strip=True)
                    if "Candidate Name" in label: info["name"] = val
                    elif "Application No" in label: info["app_no"] = val
                    elif "Roll No" in label: info["roll_no"] = val
                    elif "Test Date" in label: info["test_date"] = val
                    elif "Test Time" in label: info["test_time"] = val
            break
    return info

def calculate_marks(q_id, student_res, q_type, ans_key):
    q_id_str = str(q_id).strip()
    student_val = str(student_res).strip()
    if q_id_str == "444792191": return 4 
    if q_id_str in ans_key:
        correct_val = str(ans_key[q_id_str]).strip()
        if "dropped" in correct_val.lower(): return 4
    if q_id_str == "444792493":
        if student_val in ["4447921684", "4447921686", "4447921687"]: return 4
        return 0 if student_val in ["Not Answered", "--"] else -1
    if student_val in ["Not Answered", "--"]: return 0
    if q_id_str not in ans_key: return 0 
    return 4 if student_val == str(ans_key[q_id_str]).strip() else -1

def extract_data_from_chunks(chunks, ans_key):
    rows = []
    for chunk in chunks:
        q_id_match = re.search(r"Question ID\s*[:]\s*(\d+)", chunk)
        if not q_id_match: continue
        q_id = q_id_match.group(1)
        res = "Not Answered"
        q_type = "MCQ" if "Option 1 ID" in chunk else "SA"
        if q_type == "MCQ":
            chosen = re.search(r"Chosen Option\s*[:]\s*([1-4])", chunk)
            if chosen:
                opt_match = re.search(rf"Option {chosen.group(1)} ID\s*[:]\s*(\d+)", chunk)
                if opt_match: res = opt_match.group(1)
        else:
            given = re.search(r"Given(?:\s*Answer)?\s*[:]?\s*([-+]?\d*\.?\d+)", chunk)
            if given: res = given.group(1)
        rows.append([q_id, q_type, res, calculate_marks(q_id, res, q_type, ans_key)])
    return rows

@app.get("/")
async def health(): return {"status": "Live"}

@app.post("/calculate")
async def process_student(data: StudentInput):
    try:
        client = get_gs_client()
        ss = client.open("JEE_Predictor_Data")
        ans_tab = ss.worksheet("ANS")
        ans_key = {str(r['Question ID']): str(r['Correct Response ID']) for r in ans_tab.get_all_records()}

        link = data.url if data.url.startswith('http') else 'https://' + data.url
        soup = BeautifulSoup(requests.get(link, timeout=15, headers={'User-Agent': 'Mozilla/5.0'}).text, 'html.parser')
        cand = extract_candidate_info(soup)
        report_data = extract_data_from_chunks(re.split(r"(?=Q\.\d+)", soup.get_text(separator=' ', strip=True)), ans_key)

        m_sc = sum(i[3] for i in report_data[0:25])
        p_sc = sum(i[3] for i in report_data[25:50])
        c_sc = sum(i[3] for i in report_data[50:75])
        tot = m_sc + p_sc + c_sc

        # Individual Tab Update
        try:
            ws = ss.worksheet(str(data.phone))
            ws.clear()
        except gspread.exceptions.WorksheetNotFound:
            ws = ss.add_worksheet(title=str(data.phone), rows="100", cols="5")
        ws.update([["Question ID", "Type", "Response", "Marks"]] + report_data)

        # Smart Master Update
        master = ss.sheet1
        phones = master.col_values(1)
        row = [data.phone, cand["name"], cand["app_no"], cand["roll_no"], cand["test_date"], cand["test_time"], p_sc, c_sc, m_sc, tot, data.percentile, data.rank, data.url]
        
        if data.phone in phones:
            idx = phones.index(data.phone) + 1
            master.update(f"A{idx}:M{idx}", [row])
        else:
            master.append_row(row)

        return {"status": "success", "name": cand["name"], "total": tot, "phy": p_sc, "chem": c_sc, "math": m_sc, "app_no": cand["app_no"], "roll_no": cand["roll_no"], "test_date": cand["test_date"], "test_time": cand["test_time"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

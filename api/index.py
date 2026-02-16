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
from typing import Optional, Dict

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
    manual_data: Optional[Dict] = None

# --- INTERNAL MATH LOGIC ---
def calculate_percentile_internally(level, marks):
    ref_data = {
        1: [ { "m": 202, "p": 99.9 }, { "m": 90, "p": 95 }, { "m": 78, "p": 90 } ],
        2: [ { "m": 212, "p": 99.9 }, { "m": 98, "p": 95 }, { "m": 88, "p": 90 } ],
        3: [ { "m": 220, "p": 99.9 }, { "m": 108, "p": 95 }, { "m": 98, "p": 90 } ],
        4: [ { "m": 228, "p": 99.9 }, { "m": 118, "p": 95 }, { "m": 105, "p": 90 } ],
        5: [ { "m": 234, "p": 99.9 }, { "m": 125, "p": 95 }, { "m": 110, "p": 90 } ]
    }
    points = sorted(ref_data[level], key=lambda x: x['m'], reverse=True)
    if marks >= points[0]['m']:
        p = 99.9 + ((marks - points[0]['m']) * (0.099 / (300 - points[0]['m'])))
    else:
        p = 0
        for i in range(len(points) - 1):
            if marks >= points[i+1]['m']:
                ratio = (marks - points[i+1]['m']) / (points[i]['m'] - points[i+1]['m'])
                p = points[i+1]['p'] + ratio * (points[i]['p'] - points[i+1]['p'])
                break
        if p == 0: p = (marks / points[-1]['m']) * points[-1]['p']
    return min(99.9999, max(0, p))

def estimate_rank_internally(p):
    ranges = [
        {"p": 100, "r": 1}, {"p": 99.99, "r": 100}, {"p": 99.9, "r": 1250}, 
        {"p": 99.8, "r": 2500}, {"p": 99.5, "r": 10000}, {"p": 99.0, "r": 25000}, 
        {"p": 98.0, "r": 50000}, {"p": 95.0, "r": 100000}, {"p": 90.0, "r": 200000}
    ]
    if p >= 100: return 1
    for i in range(len(ranges) - 1):
        if p <= ranges[i]['p'] and p >= ranges[i+1]['p']:
            ratio = (p - ranges[i+1]['p']) / (ranges[i]['p'] - ranges[i+1]['p'])
            return int(ranges[i+1]['r'] - ratio * (ranges[i+1]['r'] - ranges[i]['r']))
    return int((100 - p) * 20000)

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
        
        student_res = "Not Answered"
        is_mcq = "Option 1 ID" in chunk
        q_type = "MCQ" if is_mcq else "SA"
        
        if is_mcq:
            # 1. Extract all 4 Option IDs from the chunk
            opt_ids = re.findall(r"Option [1-4] ID\s*[:]\s*(\d+)", chunk)
            
            # 2. Get the student's actual chosen ID (not just the number 1-4)
            chosen_match = re.search(r"Chosen Option\s*[:]\s*([1-4])", chunk)
            
            if chosen_match and len(opt_ids) == 4:
                chosen_idx = chosen_match.group(1)
                # Find the specific ID the student chose based on the display order
                student_res = re.search(rf"Option {chosen_idx} ID\s*[:]\s*(\d+)", chunk).group(1)
                
                # 3. Sorting Logic for the Answer Key
                if q_id in ans_key:
                    correct_pos = ans_key[q_id] # e.g., "3"
                    if correct_pos.isdigit():
                        sorted_opts = sorted(opt_ids) # Sort IDs numerically: 54, 55, 56, 57
                        idx = int(correct_pos) - 1 # Convert 1-based to 0-based index
                        
                        # The "Correct Response ID" in your sheet is actually the index 
                        # of the sorted list. We override correct_val for this calculation.
                        target_correct_id = sorted_opts[idx]
                        
                        # Compare the student's chosen ID to the sorted target ID
                        marks = 4 if student_res == target_correct_id else -1
                    else:
                        marks = calculate_marks(q_id, student_res, q_type, ans_key)
                else:
                    marks = 0
            else:
                student_res = "Not Answered"
                marks = 0
        else:
            # Section B / Numerical Logic remains the same
            given_match = re.search(r"Given(?:\s*Answer)?\s*[:]?\s*([-+]?\d*\.?\d+)", chunk)
            if given_match: student_res = given_match.group(1)
            marks = calculate_marks(q_id, student_res, q_type, ans_key)

        rows.append([q_id, q_type, student_res, marks])
    return rows

@app.get("/")
async def health(): return {"status": "Live"}

@app.post("/calculate")
async def process_student(data: StudentInput):
    try:
        p_sc, c_sc, m_sc, tot = 0, 0, 0, 0
        p_cor, p_inc, p_una = 0, 0, 0
        c_cor, c_inc, c_una = 0, 0, 0
        m_cor, m_inc, m_una = 0, 0, 0
        tot_cor, tot_inc, tot_una = 0, 0, 0
        report_data = []
        cand = {"name": "Manual Entry", "app_no": "-", "roll_no": "-", "test_date": "-", "test_time": "-"}

        if data.url != "manual_mode":
            link = data.url if data.url.startswith('http') else 'https://' + data.url
            response = requests.get(link, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(response.text, 'html.parser')
            cand = extract_candidate_info(soup)
            
            client = get_gs_client()
            ss = client.open("JEE_Predictor_Data")
            ans_tab = ss.worksheet("ANS")
            ans_key = {str(r['Question ID']): str(r['Correct Response ID']) for r in ans_tab.get_all_records()}
            
            report_data = extract_data_from_chunks(re.split(r"(?=Q\.\d+)", soup.get_text(separator=' ', strip=True)), ans_key)

            def get_section_stats(section_rows):
                # Critical Update: row[4] contains marks now
                correct = sum(1 for row in section_rows if row[4] == 4)
                incorrect = sum(1 for row in section_rows if row[4] == -1)
                unattempted = sum(1 for row in section_rows if row[2] in ["Not Answered", "--"])
                score = sum(row[4] for row in section_rows)
                return score, correct, incorrect, unattempted

            m_sc, m_cor, m_inc, m_una = get_section_stats(report_data[0:25])
            p_sc, p_cor, p_inc, p_una = get_section_stats(report_data[25:50])
            c_sc, c_cor, c_inc, c_una = get_section_stats(report_data[50:75])
            
            tot = m_sc + p_sc + c_sc
            tot_cor, tot_inc, tot_una = (m_cor + p_cor + c_cor), (m_inc + p_inc + c_inc), (m_una + p_una + c_una)

        elif data.manual_data:
            tot = int(data.manual_data.get('total', 0))

        final_p, final_r = "0.0000", "0"
        if data.percentile.isdigit():
            level = int(data.percentile)
            p_val = calculate_percentile_internally(level, tot)
            r_val = estimate_rank_internally(p_val)
            final_p, final_r = f"{p_val:.4f}", str(r_val)

            client = get_gs_client()
            ss = client.open("JEE_Predictor_Data")
            
            if data.url != "manual_mode":
                try:
                    ws = ss.worksheet(str(data.phone))
                    ws.clear()
                except:
                    ws = ss.add_worksheet(title=str(data.phone), rows="100", cols="6")
                ws.update([["Question ID", "Type", "Response", "Correct Answer", "Marks"]] + report_data)

            master = ss.sheet1
            row = [data.phone, cand["name"], cand["app_no"], cand["roll_no"], cand["test_date"], cand["test_time"], p_sc, c_sc, m_sc, tot, final_p, final_r, data.url]
            master.append_row(row)

        return {
            "status": "success", "percentile": final_p, "rank": final_r, "total": tot,
            "phy": p_sc, "p_cor": p_cor, "p_inc": p_inc, "p_una": p_una,
            "chem": c_sc, "c_cor": c_cor, "c_inc": c_inc, "c_una": c_una,
            "math": m_sc, "m_cor": m_cor, "m_inc": m_inc, "m_una": m_una,
            "tot_cor": tot_cor, "tot_inc": tot_inc, "tot_una": tot_una,
            "app_no": cand["app_no"], "roll_no": cand["roll_no"], 
            "test_date": cand["test_date"], "test_time": cand["test_time"],
            "report_data": report_data, "mode": "manual" if data.url == "manual_mode" else "link",
            "name": cand["name"]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

import os, json
from google.oauth2.service_account import Credentials
import gspread

creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
sheet_id   = os.environ["SHEET_ID"]

print("SHEET_ID length:", len(sheet_id))
print("SHEET_ID value: [" + sheet_id + "]")

creds_obj = json.loads(creds_json)
print("client_email:", creds_obj["client_email"])
print("project_id:  ", creds_obj["project_id"])

creds = Credentials.from_service_account_info(
    creds_obj,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(sheet_id)
print("SUCCESS:", sh.title)

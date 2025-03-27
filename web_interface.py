from fastapi import FastAPI, UploadFile, File, Form, Request
import logging
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import shutil
from pathlib import Path
import asyncio
from ai_csv_transformer import process_contacts, read_csv_file_async, get_file_paths, ensure_directories
from datetime import datetime
from csv_transformer import transform_for_listmonk
import httpx
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configure templates
templates = Jinja2Templates(directory="templates")

LISTMONK_BASE_URL = os.getenv('LISTMONK_BASE_URL')
LISTMONK_AUTH = (os.getenv('LISTMONK_USERNAME'), os.getenv('LISTMONK_PASSWORD'))

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload/")
async def upload_file(file: UploadFile = File(...)):
    # Define temp_file outside try block so it's available in except block
    temp_file = None
    try:
        # Ensure directories exist
        paths = get_file_paths()
        ensure_directories()
        
        # Create timestamp and unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_name = Path(file.filename).stem
        
        # Save uploaded file with timestamp
        temp_file = paths['uploads'] / f"temp_{timestamp}_{original_name}.csv"
        output_file = paths['processed'] / f"processed_{timestamp}_{original_name}.csv"
        listmonk_file = paths['listmonk'] / f"listmonk_{timestamp}_{original_name}.csv"
        
        with temp_file.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Process the file using existing transformer
        contacts = await read_csv_file_async(temp_file)
        processed, successful = await process_contacts(contacts, output_file)
        
        # Create ListMonk format
        await transform_for_listmonk(output_file, listmonk_file)
        
        # Clean up temp file
        if temp_file.exists():
            temp_file.unlink()
        
        return JSONResponse({
            "message": f"File processed successfully. Total: {processed}, Successful: {successful}, Failed: {processed - successful}",
            "filename": output_file.name,
            "listmonk_filename": listmonk_file.name
        })
    
    except Exception as e:
        import traceback
        logging.error(traceback.format_exc())
        if temp_file and temp_file.exists():
            temp_file.unlink()
        return JSONResponse({
            "error": str(e),
            "message": f"Error processing file: {str(e)}"
        }, status_code=500)

@app.get("/files/")
async def get_files():
    paths = get_file_paths()
    processed_files = list(paths['processed'].glob("processed_*.csv"))
    
    files = []
    for proc_file in processed_files:
        # Extract timestamp and original name
        parts = proc_file.stem.split('_', 2)  # Split into ['processed', 'timestamp', 'original_name']
        if len(parts) >= 3:
            timestamp = parts[1]
            original_name = parts[2]
            listmonk_file = f"listmonk_{timestamp}_{original_name}.csv"
            
            # Format timestamp for display
            try:
                dt = datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
                formatted_timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                formatted_timestamp = timestamp

            files.append({
                "timestamp": formatted_timestamp,
                "original_name": original_name,
                "processed_file": proc_file.name,
                "listmonk_file": listmonk_file
            })
    
    # Sort files by timestamp (newest first)
    files.sort(key=lambda x: x["timestamp"], reverse=True)
    return files

@app.get("/download/{filename}")
async def download_file(filename: str):
    # Determine which directory to look in based on filename prefix
    paths = get_file_paths()
    if filename.startswith("processed_"):
        file_path = paths['processed'] / filename
    elif filename.startswith("listmonk_"):
        file_path = paths['listmonk'] / filename
    else:
        file_path = paths['uploads'] / filename
    
    if not file_path.exists():
        return JSONResponse({
            "error": f"File not found: {file_path}"
        }, status_code=404)
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='text/csv'
    )

@app.post("/api/listmonk/lists")
async def create_listmonk_list(list_data: dict):
    logging.info(f"Creating Listmonk list with data: {list_data}")
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.post(
                f"{LISTMONK_BASE_URL}/api/lists",
                json=list_data,
                auth=LISTMONK_AUTH,
                timeout=30.0
            )
            response.raise_for_status()
            response_data = response.json()
            logging.info(f"Listmonk list creation response: {response_data}")
            return response_data
        except httpx.HTTPError as e:
            error_msg = f"Listmonk API error: {str(e)}"
            logging.error(error_msg)
            return JSONResponse({
                "error": error_msg
            }, status_code=500)

@app.post("/api/listmonk/import")
async def import_subscribers(
    mode: str = Form(...),
    subscription_status: str = Form(...),
    delim: str = Form(...),
    lists: str = Form(...),
    overwrite: str = Form(...),
    filename: str = Form(...)
):
    logging.info(f"Importing subscribers with mode: {mode}, subscription_status: {subscription_status}, lists: {lists}, filename: {filename}")
    paths = get_file_paths()
    file_path = paths['listmonk'] / filename
    
    logging.info(f"Expected file path: {file_path}")
    if not file_path.exists():
        error_msg = f"File not found: {file_path}"
        logging.error(error_msg)
        return JSONResponse({"error": error_msg}, status_code=404)
    
    async with httpx.AsyncClient(verify=False) as client:
        try:
            form_data = {
                'file': ('subscribers.csv', open(file_path, 'rb'), 'text/csv'),
                'mode': (None, mode),
                'subscription_status': (None, subscription_status),
                'delim': (None, delim),
                'lists': (None, lists),
                'overwrite': (None, overwrite)
            }
            
            logging.info(f"Sending form data to Listmonk: {form_data}")
            response = await client.post(
                f"{LISTMONK_BASE_URL}/api/import/subscribers",
                files=form_data,
                auth=LISTMONK_AUTH,
                timeout=30.0
            )
            response.raise_for_status()
            response_data = response.json()
            logging.info(f"Listmonk import subscribers response: {response_data}")
            return response_data
        except httpx.HTTPError as e:
            error_msg = f"Listmonk API error: {str(e)}"
            logging.error(error_msg)
            return JSONResponse({
                "error": error_msg
            }, status_code=500)
        finally:
            if 'file' in form_data:
                form_data['file'][1].close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

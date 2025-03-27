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
    async with httpx.AsyncClient(verify=False) as client:  # Disable SSL verification
        try:
            response = await client.post(
                f"{LISTMONK_BASE_URL}/formater/api/lists",
                json=list_data,
                auth=LISTMONK_AUTH,
                timeout=30.0
            )
            response.raise_for_status()  # Raise exception for 4XX/5XX status codes
            return response.json()
        except httpx.HTTPError as e:
            return JSONResponse({
                "error": f"Listmonk API error: {str(e)}"
            }, status_code=500)

@app.post("/api/listmonk/import")
async def import_subscribers(params: str = Form(...), filename: str = Form(...)):
    paths = get_file_paths()
    file_path = paths['listmonk'] / filename
    
    if not file_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    
    async with httpx.AsyncClient(verify=False) as client:  # Disable SSL verification
        try:
            files = {
                'file': ('subscribers.csv', open(file_path, 'rb'), 'text/csv'),
                'params': (None, params)
            }
            response = await client.post(
                f"{LISTMONK_BASE_URL}/formater/api/import/subscribers",
                files=files,
                auth=LISTMONK_AUTH,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            return JSONResponse({
                "error": f"Listmonk API error: {str(e)}"
            }, status_code=500)
        finally:
            if 'file' in files:
                files['file'][1].close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

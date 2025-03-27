from fastapi import FastAPI, UploadFile, File, Form, Request
import logging
import json
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

# Set a prefix for all routes
PREFIX = "/formater"

# Mount static files
app.mount(f"{PREFIX}/static", StaticFiles(directory="static"), name="static")

# Configure templates
templates = Jinja2Templates(directory="templates")

# Get Listmonk configuration from environment variables
LISTMONK_BASE_URL = os.getenv('LISTMONK_BASE_URL', 'http://localhost:9000')
LISTMONK_USERNAME = os.getenv('LISTMONK_USERNAME', 'api_user')
LISTMONK_PASSWORD = os.getenv('LISTMONK_PASSWORD', 'token')
LISTMONK_AUTH = (LISTMONK_USERNAME, LISTMONK_PASSWORD)

# Log Listmonk configuration (without sensitive data)
logging.info(f"Listmonk API URL: {LISTMONK_BASE_URL}")

# Flag to track if Listmonk is available
listmonk_available = False

async def check_listmonk_availability():
    """Check if the Listmonk server is available"""
    global listmonk_available
    
    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=5.0
        ) as client:
            # Try to connect to the Listmonk API
            response = await client.get(
                f"{LISTMONK_BASE_URL}/api/health",
                auth=LISTMONK_AUTH
            )
            
            if response.status_code == 200:
                listmonk_available = True
                logging.info("Listmonk server is available")
            else:
                listmonk_available = False
                logging.warning(f"Listmonk server returned status code {response.status_code}")
                
    except Exception as e:
        listmonk_available = False
        logging.warning(f"Listmonk server is not available: {str(e)}")
    
    return listmonk_available

# Schedule periodic checks of Listmonk availability
@app.on_event("startup")
async def startup_event():
    # Check Listmonk availability on startup
    await check_listmonk_availability()

@app.get(f"{PREFIX}/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post(f"{PREFIX}/upload/")
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

@app.get(f"{PREFIX}/files/")
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

@app.get(f"{PREFIX}/download/{{filename}}")
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

async def make_listmonk_request(method, endpoint, **kwargs):
    """Helper function to make requests to the Listmonk API with retry logic"""
    max_retries = 3
    retry_delay = 1  # seconds
    
    for attempt in range(max_retries):
        try:
            # Create a new client for each attempt
            async with httpx.AsyncClient(
                verify=False,  # Disable SSL verification
                timeout=30.0,
                base_url=LISTMONK_BASE_URL
            ) as client:
                # Add authentication
                kwargs['auth'] = LISTMONK_AUTH
                
                # Make the request
                logging.info(f"Making {method} request to {endpoint} (attempt {attempt+1}/{max_retries})")
                if method.lower() == 'get':
                    response = await client.get(endpoint, **kwargs)
                elif method.lower() == 'post':
                    response = await client.post(endpoint, **kwargs)
                elif method.lower() == 'put':
                    response = await client.put(endpoint, **kwargs)
                elif method.lower() == 'delete':
                    response = await client.delete(endpoint, **kwargs)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                # Check for errors
                response.raise_for_status()
                
                # Return the response
                return response
                
        except httpx.HTTPStatusError as e:
            # Server responded with an error status code
            error_msg = f"Listmonk API HTTP error: {e.response.status_code} - {str(e)}"
            logging.error(error_msg)
            
            # Try to get more details from the response
            try:
                error_detail = e.response.json()
                logging.error(f"Error details: {error_detail}")
            except:
                pass
            
            # If this is the last attempt, re-raise the exception
            if attempt == max_retries - 1:
                raise
                
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            # Connection error or timeout
            error_msg = f"Connection error: {str(e)}"
            logging.error(error_msg)
            
            # If this is the last attempt, re-raise the exception
            if attempt == max_retries - 1:
                raise
                
        except Exception as e:
            # Other unexpected errors
            error_msg = f"Unexpected error: {str(e)}"
            logging.error(error_msg)
            
            # If this is the last attempt, re-raise the exception
            if attempt == max_retries - 1:
                raise
        
        # Wait before retrying
        if attempt < max_retries - 1:
            retry_delay_with_jitter = retry_delay * (1 + 0.1 * attempt)  # Add jitter
            logging.info(f"Retrying in {retry_delay_with_jitter:.1f} seconds...")
            await asyncio.sleep(retry_delay_with_jitter)
            retry_delay *= 2  # Exponential backoff

@app.get(f"{PREFIX}/api/listmonk/status")
async def get_listmonk_status():
    """Endpoint to check if Listmonk is available"""
    is_available = await check_listmonk_availability()
    return {
        "available": is_available,
        "url": LISTMONK_BASE_URL
    }

@app.post(f"{PREFIX}/api/listmonk/lists")
async def create_listmonk_list(list_data: dict):
    # Check if Listmonk is available
    if not listmonk_available and not await check_listmonk_availability():
        error_msg = f"Listmonk server is not available at {LISTMONK_BASE_URL}. Please check your configuration."
        logging.error(error_msg)
        return JSONResponse({
            "error": error_msg,
            "server_unavailable": True
        }, status_code=503)  # Service Unavailable
    
    try:
        # Log the request for debugging
        logging.info(f"Creating Listmonk list with data: {list_data}")
        
        # Make the request to the Listmonk API
        response = await make_listmonk_request(
            'post',
            '/api/lists',
            json=list_data
        )
        
        # Get the response data
        response_data = response.json()
        logging.info(f"Listmonk list creation response: {response_data}")
        
        # Ensure the response has the expected structure
        if 'data' not in response_data:
            # If the response doesn't have a 'data' field, wrap it in one
            # This ensures consistent structure for the frontend
            return {"data": response_data}
        
        return response_data
        
    except httpx.HTTPError as e:
        error_msg = f"Listmonk API error: {str(e)}"
        logging.error(error_msg)
        return JSONResponse({
            "error": error_msg
        }, status_code=500)
        
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logging.error(error_msg)
        return JSONResponse({
            "error": error_msg
        }, status_code=500)

@app.post(f"{PREFIX}/api/listmonk/import")
async def import_subscribers(params: str = Form(...), filename: str = Form(...)):
    # Check if Listmonk is available
    if not listmonk_available and not await check_listmonk_availability():
        error_msg = f"Listmonk server is not available at {LISTMONK_BASE_URL}. Please check your configuration."
        logging.error(error_msg)
        return JSONResponse({
            "error": error_msg,
            "server_unavailable": True
        }, status_code=503)  # Service Unavailable
    
    paths = get_file_paths()
    file_path = paths['listmonk'] / filename
    
    if not file_path.exists():
        error_msg = f"File not found: {filename}"
        logging.error(error_msg)
        return JSONResponse({"error": error_msg}, status_code=404)
    
    # Log the import parameters
    try:
        params_dict = json.loads(params)
        logging.info(f"Importing subscribers with params: {params_dict}")
    except:
        logging.info(f"Importing subscribers with params: {params}")
    
    # Custom implementation for file upload with retries
    max_retries = 3
    retry_delay = 1  # seconds
    
    for attempt in range(max_retries):
        file_handle = None
        try:
            file_handle = open(file_path, 'rb')
            files = {
                'file': ('subscribers.csv', file_handle, 'text/csv'),
                'params': (None, params)
            }
            
            logging.info(f"Sending import request to Listmonk API (attempt {attempt+1}/{max_retries})")
            
            # Create a new client for each attempt
            async with httpx.AsyncClient(
                verify=False,  # Disable SSL verification
                timeout=60.0,  # Increased timeout for large imports
                base_url=LISTMONK_BASE_URL
            ) as client:
                response = await client.post(
                    '/api/import/subscribers',
                    files=files,
                    auth=LISTMONK_AUTH
                )
                response.raise_for_status()
                
                response_data = response.json()
                logging.info(f"Listmonk import response: {response_data}")
                return response_data
                
        except httpx.HTTPStatusError as e:
            # Server responded with an error status code
            error_msg = f"Listmonk API HTTP error: {e.response.status_code} - {str(e)}"
            logging.error(error_msg)
            
            # Try to get more details from the response
            try:
                error_detail = e.response.json()
                logging.error(f"Error details: {error_detail}")
            except:
                pass
            
            # If this is the last attempt, return error response
            if attempt == max_retries - 1:
                return JSONResponse({
                    "error": error_msg
                }, status_code=500)
                
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            # Connection error or timeout
            error_msg = f"Connection error: {str(e)}"
            logging.error(error_msg)
            
            # If this is the last attempt, return error response
            if attempt == max_retries - 1:
                return JSONResponse({
                    "error": error_msg
                }, status_code=500)
                
        except Exception as e:
            # Other unexpected errors
            error_msg = f"Unexpected error during import: {str(e)}"
            logging.error(error_msg)
            
            # If this is the last attempt, return error response
            if attempt == max_retries - 1:
                return JSONResponse({
                    "error": error_msg
                }, status_code=500)
                
        finally:
            # Close the file handle if it was opened
            if file_handle:
                file_handle.close()
        
        # Wait before retrying
        if attempt < max_retries - 1:
            retry_delay_with_jitter = retry_delay * (1 + 0.1 * attempt)  # Add jitter
            logging.info(f"Retrying import in {retry_delay_with_jitter:.1f} seconds...")
            await asyncio.sleep(retry_delay_with_jitter)
            retry_delay *= 2  # Exponential backoff

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

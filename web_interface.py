from fastapi import FastAPI, UploadFile, File
import logging
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import shutil
from pathlib import Path
import asyncio
from ai_csv_transformer import process_contacts, read_csv_file_async, get_file_paths, ensure_directories
from datetime import datetime
from csv_transformer import transform_for_listmonk

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)

app = FastAPI()

# HTML template for the upload page
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>CSV File Processor</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .container { max-width: 800px; margin: 0 auto; }
        .upload-form { 
            border: 2px dashed #ccc; 
            padding: 20px; 
            text-align: center; 
            margin-bottom: 20px;
        }
        .status { margin-top: 20px; }
        #progressBar {
            width: 100%;
            height: 20px;
            background-color: #f0f0f0;
            border-radius: 10px;
            display: none;
        }
        #progressBar div {
            height: 100%;
            background-color: #4CAF50;
            border-radius: 10px;
            width: 0%;
            transition: width 0.5s;
        }
        .download-link {
            display: inline-block;
            margin: 5px;
            padding: 5px 10px;
            background-color: #4CAF50;
            color: white;
            text-decoration: none;
            border-radius: 5px;
        }
        .download-link:hover {
            background-color: #45a049;
        }
        #downloads, #dashboard {
            margin-top: 20px;
            text-align: left;
        }
        .dashboard-title {
            margin-top: 30px;
            border-bottom: 2px solid #ccc;
            padding-bottom: 10px;
        }
        .file-list {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }
        .file-list th, .file-list td {
            padding: 8px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        .file-list tr:hover {
            background-color: #f5f5f5;
        }
    </style>
</head>
<body>
    <div class="container">
            </a>
        </div>
    </div>
    <script>
        document.getElementById('uploadForm').onsubmit = async (e) => {
            e.preventDefault();
            
            const formData = new FormData(e.target);
            const status = document.getElementById('status');
            const progressBar = document.getElementById('progressBar');
            const progressDiv = progressBar.querySelector('div');
            const downloads = document.getElementById('downloads');
            
            status.textContent = 'Uploading and processing file...';
            progressBar.style.display = 'block';
            progressDiv.style.width = '50%';
            downloads.style.display = 'none';

            try {
                const response = await fetch('/upload/', {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                progressDiv.style.width = '100%';
                
                if (response.ok) {
                    status.textContent = `Processing complete! ${result.message}`;
                    
                    // Show download links
                    downloads.style.display = 'block';
                    document.getElementById('downloadLink').href = `/download/${result.filename}`;
                    document.getElementById('downloadLink').style.display = 'inline-block';
                    document.getElementById('downloadLinkListmonk').href = `/download/${result.listmonk_filename}`;
                    document.getElementById('downloadLinkListmonk').style.display = 'inline-block';
                } else {
                    // Server returned an error
                    status.textContent = `Error: ${result.message || result.error || 'Unknown error'}`;
                    progressDiv.style.backgroundColor = '#ff0000';
                    downloads.style.display = 'none';
                }
            } catch (error) {
                status.textContent = 'Error processing file: ' + error;
                progressDiv.style.backgroundColor = '#ff0000';
                downloads.style.display = 'none';
            }
        };
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return HTML_TEMPLATE

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import shutil
from pathlib import Path
import asyncio
from ai_csv_transformer import process_contacts, read_csv_file_async
from datetime import datetime
from csv_transformer import transform_for_listmonk

app = FastAPI()

# HTML template for the upload page
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>CSV File Processor</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        .container { max-width: 600px; margin: 0 auto; }
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
            margin-top: 20px;
            margin-right: 10px;
            padding: 10px 20px;
            background-color: #4CAF50;
            color: white;
            text-decoration: none;
            border-radius: 5px;
        }
        .download-link:hover {
            background-color: #45a049;
        }
        #downloads {
            margin-top: 20px;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>CSV File Processor</h1>
        <div class="upload-form">
            <form id="uploadForm" enctype="multipart/form-data">
                <input type="file" name="file" accept=".csv" required>
                <button type="submit">Process File</button>
            </form>
        </div>
        <div id="progressBar">
            <div></div>
        </div>
        <div id="status" class="status"></div>
        <div id="downloads" style="display: none;">
            <a id="downloadLink" class="download-link" href="#">
                <i class="fas fa-download"></i> Download Processed File
            </a>
            <a id="downloadLinkListmonk" class="download-link" href="#">
                <i class="fas fa-download"></i> Download ListMonk Format
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
                status.textContent = `Processing complete! ${result.message}`;
                
                // Show download links
                downloads.style.display = 'block';
                document.getElementById('downloadLink').href = `/download/${result.filename}`;
                document.getElementById('downloadLink').style.display = 'inline-block';
                document.getElementById('downloadLinkListmonk').href = `/download/${result.listmonk_filename}`;
                document.getElementById('downloadLinkListmonk').style.display = 'inline-block';
            } catch (error) {
                status.textContent = 'Error processing file: ' + error;
                progressDiv.style.backgroundColor = '#ff0000';
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
    try:
        # Create timestamp and unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_name = Path(file.filename).stem
        
        # Save uploaded file with timestamp
        temp_file = Path(f"temp_{timestamp}_{original_name}.csv")
        output_file = Path(f"processed_{timestamp}_{original_name}.csv")
        listmonk_file = Path(f"listmonk_{timestamp}_{original_name}.csv")
        
        with temp_file.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Process the file using existing transformer
        contacts = await read_csv_file_async(temp_file)
        processed, successful = await process_contacts(contacts, output_file)
        
        # Create ListMonk format
        await transform_for_listmonk(output_file, listmonk_file)
        
        # Clean up temp file
        temp_file.unlink()
        
        return JSONResponse({
            "message": f"File processed successfully. Total: {processed}, Successful: {successful}, Failed: {processed - successful}",
            "filename": output_file.name,
            "listmonk_filename": listmonk_file.name
        })
    
    except Exception as e:
        if temp_file.exists():
            temp_file.unlink()
        return JSONResponse({
            "error": str(e)
        }, status_code=500)

@app.get("/download/{filename}")
async def download_file(filename: str):
    file_path = Path(filename)
    if not file_path.exists():
        return JSONResponse({
            "error": "File not found"
        }, status_code=404)
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='text/csv'
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

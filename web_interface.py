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
        <div id="dashboard">
            <h2 class="dashboard-title">Previously Processed Files</h2>
            <table class="file-list">
                <thead>
                    <tr>
                        <th>Timestamp</th>
                        <th>Original Name</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="fileList">
                    <!-- Files will be listed here -->
                </tbody>
            </table>
        </div>
    </div>
    <script>
        // Existing upload form handler
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

        // Add function to load file list
        async function loadFileList() {
            try {
                const response = await fetch('/files/');
                const files = await response.json();
                const fileList = await document.getElementById('fileList');
                fileList.innerHTML = '';

                files.forEach(file => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${file.timestamp}</td>
                        <td>${file.original_name}</td>
                        <td>
                            <a href="/download/${file.processed_file}" class="download-link">Processed</a>
                            <a href="/download/${file.listmonk_file}" class="download-link">ListMonk</a>
                        </td>
                    `;
                    fileList.appendChild(row);
                });
            } catch (error) {
                console.error('Error loading file list:', error);
            }
        }

        // Load file list on page load and after successful upload
        loadFileList();
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

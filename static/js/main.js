// Handle file upload
document.getElementById('uploadForm').onsubmit = async (e) => {
    e.preventDefault();
    
    const formData = new FormData(e.target);
    console.log('Starting file upload with formData:', Object.fromEntries(formData));
    
    const status = document.getElementById('status');
    const progressBar = document.getElementById('progressBar');
    const progressDiv = progressBar.querySelector('div');
    const downloads = document.getElementById('downloads');
    
    status.textContent = 'Uploading and processing file...';
    progressBar.style.display = 'block';
    progressDiv.style.width = '50%';
    downloads.style.display = 'none';

    try {
        const response = await fetch('/formater/upload/', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        console.log('Upload response:', result);
        progressDiv.style.width = '100%';
        
        if (response.ok) {
            status.textContent = `Processing complete! ${result.message}`;
            
            // Show download links
            downloads.style.display = 'block';
            document.getElementById('downloadLink').href = `/formater/download/${result.filename}`;
            document.getElementById('downloadLink').style.display = 'inline-block';
            document.getElementById('downloadLinkListmonk').href = `/formater/download/${result.listmonk_filename}`;
            document.getElementById('downloadLinkListmonk').style.display = 'inline-block';
        } else {
            status.textContent = `Error: ${result.message || result.error || 'Unknown error'}`;
            progressDiv.style.backgroundColor = '#ff0000';
            downloads.style.display = 'none';
        }
    } catch (error) {
        console.error('Upload error:', error);
        status.textContent = 'Error processing file: ' + error;
        progressDiv.style.backgroundColor = '#ff0000';
        downloads.style.display = 'none';
    }
};

// Load file list function
async function loadFileList() {
    console.log('Loading file list...');
    try {
        const response = await fetch('/formater/files/');
        const files = await response.json();
        console.log('Retrieved files:', files);
        const fileList = document.getElementById('fileList');
        fileList.innerHTML = '';

        files.forEach(file => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${file.timestamp}</td>
                <td>${file.original_name}</td>
                <td>
                    <a href="/formater/download/${file.processed_file}" class="download-link">Processed</a>
                    <a href="/formater/download/${file.listmonk_file}" class="download-link">ListMonk</a>
                </td>
            `;
            fileList.appendChild(row);
        });
    } catch (error) {
        console.error('Error loading file list:', error);
    }
}

// Load file list on page load
loadFileList();

// Add Listmonk integration functions
async function createListmonkList(listData) {
    console.log('Creating Listmonk list with data:', listData);
    try {
        const response = await fetch('/formater/api/listmonk/lists', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(listData)
        });
        const result = await response.json();
        console.log('Listmonk list creation response:', result);
        return result;
    } catch (error) {
        console.error('Listmonk list creation error:', error);
        throw new Error(`Failed to create list: ${error.message}`);
    }
}

async function pushToListmonk(listId, filename) {
    console.log('Pushing to Listmonk with listId:', listId, 'filename:', filename);
    try {
        const params = {
            mode: 'subscribe',
            subscription_status: 'confirmed',
            delim: ',',
            lists: [listId],
            overwrite: true
        };
        console.log('Import parameters:', params);

        const formData = new FormData();
        formData.append('params', JSON.stringify(params));
        formData.append('filename', filename);

        const response = await fetch('/formater/api/listmonk/import', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();
        console.log('Listmonk import response:', result);
        return result;
    } catch (error) {
        console.error('Listmonk import error:', error);
        throw new Error(`Failed to import subscribers: ${error.message}`);
    }
}

// Add event listener for Listmonk push button
document.getElementById('pushToListmonk').addEventListener('click', async () => {
    console.log('Starting Listmonk push process');
    const statusElem = document.getElementById('listmonkStatus');
    const listName = document.getElementById('listName').value;
    const listType = document.getElementById('listType').value;
    const optinType = document.getElementById('optinType').value;
    const filename = document.getElementById('downloadLinkListmonk').getAttribute('href').split('/').pop();

    console.log('Listmonk push parameters:', { listName, listType, optinType, filename });

    try {
        statusElem.textContent = 'Creating list...';
        
        const listResult = await createListmonkList({
            name: listName,
            type: listType,
            optin: optinType
        });
        console.log('List creation result:', listResult);

        statusElem.textContent = 'Importing subscribers...';
        const importResult = await pushToListmonk(listResult.data.id, filename);
        console.log('Import result:', importResult);
        
        statusElem.textContent = 'Successfully imported subscribers to Listmonk!';
    } catch (error) {
        console.error('Listmonk push error:', error);
        statusElem.textContent = `Error: ${error.message}`;
    }
});

// Handle file upload
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
        const response = await fetch('/formater/upload/', {
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

// Load file list function
async function loadFileList() {
    try {
        const response = await fetch('/formater/files/');
        const files = await response.json();
        const fileList = document.getElementById('fileList');
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

// Load file list on page load
loadFileList();

// Add Listmonk integration functions
async function createListmonkList(listData) {
    try {
        const response = await fetch('/formater/api/listmonk/lists', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(listData)
        });
        return await response.json();
    } catch (error) {
        throw new Error(`Failed to create list: ${error.message}`);
    }
}

async function pushToListmonk(listId, filename) {
    try {
        const params = {
            mode: 'subscribe',
            subscription_status: 'confirmed',
            delim: ',',
            lists: [listId],
            overwrite: true
        };

        const formData = new FormData();
        formData.append('params', JSON.stringify(params));
        formData.append('filename', filename);

        const response = await fetch('/formater/api/listmonk/import', {
            method: 'POST',
            body: formData
        });

        return await response.json();
    } catch (error) {
        throw new Error(`Failed to import subscribers: ${error.message}`);
    }
}

// Add event listener for Listmonk push button
document.getElementById('pushToListmonk').addEventListener('click', async () => {
    const statusElem = document.getElementById('listmonkStatus');
    const listName = document.getElementById('listName').value;
    const listType = document.getElementById('listType').value;
    const optinType = document.getElementById('optinType').value;
    const filename = document.getElementById('downloadLinkListmonk').getAttribute('href').split('/').pop();

    try {
        statusElem.textContent = 'Creating list...';
        const listResult = await createListmonkList({
            name: listName,
            type: listType,
            optin: optinType
        });

        // Check if listResult has the expected structure
        if (!listResult) {
            throw new Error('No response received from server');
        }
        
        if (listResult.error) {
            throw new Error(listResult.error);
        }
        
        // Handle different response structures
        let listId;
        if (listResult.data && listResult.data.id) {
            // Standard structure: { data: { id: ... } }
            listId = listResult.data.id;
        } else if (listResult.id) {
            // Alternative structure: { id: ... }
            listId = listResult.id;
        } else {
            console.error('Unexpected list creation response:', listResult);
            throw new Error('Could not determine list ID from server response');
        }

        statusElem.textContent = 'Importing subscribers...';
        const importResult = await pushToListmonk(listId, filename);
        
        statusElem.textContent = 'Successfully imported subscribers to Listmonk!';
    } catch (error) {
        console.error('Listmonk push error:', error);
        statusElem.textContent = `Error: ${error.message}`;
    }
});

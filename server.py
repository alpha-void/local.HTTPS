#=========<Code Start>==========#

from flask import Flask, request, send_file, jsonify, render_template_string, redirect
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import ssl
import json
import threading
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import argparse
import configparser
import base64

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
socketio = SocketIO(app, cors_allowed_origins="*")
CORS(app)

# Configuration
CONFIG_FILE = 'server_config.ini'
DEFAULT_CONFIG = {
    'Server': {
        'host': '0.0.0.0',
        'port': '443',
        'upload_folder': 'uploads',
        'cert_path': 'certificate.pem',
        'key_path': 'private_key.pem'
    }
}

def load_config():
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
    else:
        config.read_dict(DEFAULT_CONFIG)
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
    return config

def save_config(config):
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)

config = load_config()

# Setup upload folder
UPLOAD_FOLDER = config['Server']['upload_folder']
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024 * 1024  # 64GB max-size

# Improved FileHandler with debouncing
class FileHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_modified = 0
        self.delay = 1  # 1 second debounce delay

    def on_any_event(self, event):
        if event.is_directory:
            return

        # Implement debouncing
        current_time = time.time()
        if current_time - self.last_modified < self.delay:
            return
        
        self.last_modified = current_time
        
        # Get updated file list
        files = []
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            try:
                size = os.path.getsize(filepath)
                files.append({
                    'name': filename,
                    'size': size,
                    'size_readable': convert_size(size)
                })
            except OSError:
                continue  # Skip files that can't be accessed
                
        # Emit update through Socket.IO
        socketio.emit('files_update', {'files': files}, namespace='/')

def start_file_monitor():
    event_handler = FileHandler()
    observer = Observer()
    observer.schedule(event_handler, UPLOAD_FOLDER, recursive=False)
    observer.start()
    return observer

# Certificate management
def generate_ssl_certificate(cert_path, key_path, country="US", state="State", 
                           locality="City", org="Organization", 
                           common_name="localhost", days=365):
    cmd = (
        f'openssl req -x509 -newkey rsa:4096 -nodes '
        f'-out {cert_path} -keyout {key_path} '
        f'-days {days} -subj "/C={country}/ST={state}/L={locality}'
        f'/O={org}/CN={common_name}"'
    )
    os.system(cmd)

def setup_ssl_context(cert_path, key_path):
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        print("Generating SSL certificate...")
        generate_ssl_certificate(cert_path, key_path)
        print("SSL certificate generated.")
    
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(cert_path, key_path)
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.set_ciphers('ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384')
    ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    ssl_context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
    
    return ssl_context

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file:
        filename = secure_filename(file.filename)
        try:
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            return jsonify({'message': 'File uploaded successfully'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

# HTML template with enhanced multiple file upload support
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Secure File Transfer</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background-color: #272727;
            color: white
        }
        .container {
            background-color: #191919;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .upload-area {
            border: 2px dashed #ccc;
            padding: 20px;
            text-align: center;
            margin: 20px 0;
            border-radius: 10px;
            position: relative;
        }
        .upload-area.dragover {
            background-color: #e1f5fe;
            border-color: #2196f3;
        }
        .upload-list {
            margin: 20px 0;
        }
        .upload-item {
            margin: 10px 0;
            padding: 10px;
            background-color: #f8f9fa;
            border-radius: 4px;
            position: relative;
            color: black;
        }
        .progress-outer {
            width: 100%;
            height: 20px;
            background-color: #f0f0f0;
            border-radius: 10px;
            overflow: hidden;
            margin: 5px 0;
        }
        .progress-inner {
            width: 0%;
            height: 100%;
            background-color: #4CAF50;
            transition: width 0.3s ease;
        }
        .file-list {
            margin-top: 20px;
        }
        .file-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px;
            border-bottom: 1px solid #eee;
        }
        .button {
            background-color: #4CAF50;
            color: white;
            padding: 8px 16px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        .button:hover {
            background-color: #45a049;
        }
        .cancel-button {
            background-color: #dc3545;
            margin-left: 10px;
        }
        .status {
            margin-top: 10px;
            padding: 10px;
            border-radius: 4px;
            display: none;
        }
        .success { background-color: #dff0d8; color: #3c763d; }
        .error { background-color: #f2dede; color: #a94442; }
        .upload-counter {
            position: absolute;
            top: 8px;
            right: 8px;
            background-color: #6c757d;
            color: white;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 0.8em;
        }
        .size-warning {
            color: #856404;
            background-color: #fff3cd;
            padding: 10px;
            margin: 10px 0;
            border-radius: 4px;
            display: none;
        }

        .settings-panel {
            margin: 20px 0;
            padding: 20px;
            background-color: #f8f9fa;
            border-radius: 8px;
            border: 1px solid #dee2e6;
        }
        
        .settings-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        
        .setting-item {
            padding: 10px;
            background-color: white;
            border-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        
        .setting-item label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: black;
        }
        
        .setting-item input {
            width: 95%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-bottom: 10px;
        }
        
        .certificate-upload {
            border: 2px dashed #ccc;
            padding: 20px;
            text-align: center;
            margin: 10px 0;
            border-radius: 4px;
        }
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
            z-index: 1000;
        }
        
        .modal-content {
            position: relative;
            background-color: #1c1c1c;
            margin: 1.5% auto;
            padding: 20px;
            width: 80%;
            max-width: 600px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        
        .close-button {
            position: absolute;
            right: 10px;
            top: 10px;
            font-size: 24px;
            cursor: pointer;
            color: #666;
        }
        
        .tab-container {
            margin: 20px 0;
        }
        
        .tabs {
            display: flex;
            border-bottom: 2px solid #ddd;
            margin-bottom: 20px;
        }
        
        .tab {
            padding: 10px 20px;
            cursor: pointer;
            border: none;
            background: none;
            color: white;
            font-size: 16px;
        }
        
        .tab.active {
            border-bottom: 4px solid #4CAF50;
            margin-bottom: 0px;
            color: #4CAF50;
        }
        
        .tab-content {
            display: none;
        }
        
        .tab-content.active {
            display: block;
            color: black;
        }
        
        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 5px;
        }

        .server-status-container {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px;
            background-color: #004686;
            border-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }

        .status-wrapper {
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .server-settings-btn {
            margin-left: auto;
        }
        
        .status-indicator.connected {
            background-color: #00c308;
        }
        
        .status-indicator.disconnected {
            background-color: #dc3545;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="server-status-container">
            <div class="status-wrapper">
                <span class="status-indicator" id="serverStatus"></span>
                <span id="serverStatusText">Connecting...</span>
            </div>
            <button class="button server-settings-btn" onclick="toggleSettings()">
                Server Settings
            </button>
        </div>

        <!-- Settings Modal -->
        <div id="settingsModal" class="modal">
            <div class="modal-content">
                <span class="close-button" onclick="toggleSettings()">&times;</span>
                <h2>Server Settings</h2>
                
                <div class="tabs">
                    <button class="tab active" onclick="showTab('server')">Server</button>
                    <button class="tab" onclick="showTab('certificates')">Certificates</button>
                </div>
                
                <div id="serverTab" class="tab-content active">
                    <div class="settings-grid">
                        <div class="setting-item">
                            <label for="hostInput">Host Address:</label>
                            <input type="text" id="hostInput" placeholder="0.0.0.0">
                        </div>
                        <div class="setting-item">
                            <label for="portInput">Port:</label>
                            <input type="number" id="portInput" placeholder="443">
                        </div>
                    </div>
                </div>
                
                <div id="certificatesTab" class="tab-content">
                    <div class="setting-item">
                        <h3>SSL Certificate</h3>
                        <div class="certificate-upload">
                            <p>Upload Certificate File (.pem)</p>
                            <input type="file" id="certFile" accept=".pem,.crt">
                        </div>
                        
                        <h3>Private Key</h3>
                        <div class="certificate-upload">
                            <p>Upload Private Key File (.pem)</p>
                            <input type="file" id="keyFile" accept=".pem,.key">
                        </div>
                        
                        <button class="button" onclick="generateNewCert()">
                            Generate New Certificate
                        </button>
                    </div>
                </div>
                
                <div style="margin-top: 20px;">
                    <button class="button" onclick="saveSettings()">Save Changes</button>
                    <button class="button cancel-button" onclick="toggleSettings()">Cancel</button>
                </div>
            </div>
        </div>
        <h1>Secure File Transfer</h1>
        <div id="sizeWarning" class="size-warning"></div>
        
        <div class="upload-area" id="dropZone">
            <div class="upload-counter" id="uploadCounter">0 files selected</div>
            <p>Drag & Drop files here or click to select<br>
            <small>Maximum total size: 64GB</small></p>
            <input type="file" id="fileInput" multiple style="display: none">
            <button class="button" onclick="document.getElementById('fileInput').click()">
                Select Files
            </button>
        </div>
        
        <div id="uploadList" class="upload-list"></div>
        <div id="status" class="status"></div>
        
        <div id="uploadControls" style="display: none; margin: 20px 0;">
            <button class="button" id="uploadButton">Upload All Files</button>
            <button class="button cancel-button" id="cancelButton">Cancel All</button>
        </div>
        
        <div class="file-list" id="fileList"></div>
    </div>

    <script>
        const API_BASE = window.location.origin;
        const MAX_TOTAL_SIZE = 64 * 1024 * 1024 * 1024; // 64GB in bytes
        let selectedFiles = new Map();
        let activeUploads = new Map();
        let totalSize = 0;
        
        const dropZone = document.getElementById('dropZone');
        const fileInput = document.getElementById('fileInput');
        const uploadList = document.getElementById('uploadList');
        const uploadControls = document.getElementById('uploadControls');
        const uploadButton = document.getElementById('uploadButton');
        const cancelButton = document.getElementById('cancelButton');
        const uploadCounter = document.getElementById('uploadCounter');
        const sizeWarning = document.getElementById('sizeWarning');
        
        function updateUploadCounter() {
            uploadCounter.textContent = `${selectedFiles.size} files selected`;
        }
        
        function formatSize(bytes) {
            const units = ['B', 'KB', 'MB', 'GB'];
            let size = bytes;
            let unitIndex = 0;
            while (size >= 1024 && unitIndex < units.length - 1) {
                size /= 1024;
                unitIndex++;
            }
            return `${size.toFixed(2)} ${units[unitIndex]}`;
        }
        
        function checkTotalSize() {
            if (totalSize > MAX_TOTAL_SIZE) {
                sizeWarning.textContent = `Total size (${formatSize(totalSize)}) exceeds maximum limit of 64GB`;
                sizeWarning.style.display = 'block';
                uploadButton.disabled = true;
            } else {
                sizeWarning.style.display = 'none';
                uploadButton.disabled = false;
            }
        }
        
        function addFiles(files) {
            for (const file of files) {
                if (!selectedFiles.has(file.name)) {
                    selectedFiles.set(file.name, file);
                    totalSize += file.size;
                    
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'upload-item';
                    itemDiv.innerHTML = `
                        <div>${file.name} (${formatSize(file.size)})</div>
                        <div class="progress-outer">
                            <div class="progress-inner" id="progress-${file.name}"></div>
                        </div>
                    `;
                    uploadList.appendChild(itemDiv);
                }
            }
            
            updateUploadCounter();
            checkTotalSize();
            uploadControls.style.display = selectedFiles.size > 0 ? 'block' : 'none';
        }
        
        // Event Listeners
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });
        
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });
        
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            addFiles(e.dataTransfer.files);
        });
        
        fileInput.addEventListener('change', (e) => {
            addFiles(e.target.files);
            fileInput.value = ''; // Reset input for repeated selections
        });
        
        function showStatus(message, isError = false) {
            const status = document.getElementById('status');
            status.textContent = message;
            status.style.display = 'block';
            status.className = `status ${isError ? 'error' : 'success'}`;
            setTimeout(() => status.style.display = 'none', 3000);
        }
        
        async function uploadFile(file) {
            const formData = new FormData();
            formData.append('file', file);
            
            const xhr = new XMLHttpRequest();
            const progressBar = document.getElementById(`progress-${file.name}`);
            
            return new Promise((resolve, reject) => {
                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable) {
                        const percent = (e.loaded / e.total) * 100;
                        progressBar.style.width = percent + '%';
                    }
                });
                
                xhr.onload = () => {
                    if (xhr.status === 200) {
                        resolve();
                    } else {
                        reject(new Error(xhr.statusText));
                    }
                };
                
                xhr.onerror = () => reject(new Error('Network error'));
                
                xhr.open('POST', `${API_BASE}/upload`, true);
                xhr.send(formData);
                activeUploads.set(file.name, xhr);
            });
        }
        
        uploadButton.addEventListener('click', async () => {
            uploadButton.disabled = true;
            let completed = 0;
            const totalFiles = selectedFiles.size;
            
            for (const [filename, file] of selectedFiles) {
                try {
                    await uploadFile(file);
                    completed++;
                    showStatus(`Uploaded ${completed}/${totalFiles} files`);
                } catch (error) {
                    showStatus(`Error uploading ${filename}: ${error.message}`, true);
                }
            }
            
            selectedFiles.clear();
            activeUploads.clear();
            totalSize = 0;
            uploadList.innerHTML = '';
            uploadControls.style.display = 'none';
            updateUploadCounter();
            loadFileList();
        });
        
        cancelButton.addEventListener('click', () => {
            activeUploads.forEach(xhr => xhr.abort());
            selectedFiles.clear();
            activeUploads.clear();
            totalSize = 0;
            uploadList.innerHTML = '';
            uploadControls.style.display = 'none';
            updateUploadCounter();
            sizeWarning.style.display = 'none';
        });
        
        async function loadFileList() {
            try {
                const response = await fetch(`${API_BASE}/files`);
                const files = await response.json();
                
                const fileList = document.getElementById('fileList');
                fileList.innerHTML = '<h2>Uploaded Files</h2>';
                
                files.forEach(file => {
                    const fileItem = document.createElement('div');
                    fileItem.className = 'file-item';
                    fileItem.innerHTML = `
                        <div>
                            <strong>${file.name}</strong>
                            <br>
                            <small>${file.size_readable}</small>
                        </div>
                        <button class="button" onclick="downloadFile('${file.name}')">Download</button>
                    `;
                    fileList.appendChild(fileItem);
                });
            } catch (error) {
                showStatus('Error loading file list: ' + error.message, true);
            }
        }
        
        function downloadFile(filename) {
            window.location.href = `${API_BASE}/download/${filename}`;
        }
        
        // Load initial file list
        loadFileList();

    // Initialize Socket.IO
        let socket;
        initializeSocket();
        
        function initializeSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const socketUrl = `${protocol}//${window.location.host}`;
            socket = io(socketUrl);
            
            socket.on('connect', () => {
                updateServerStatus(true);
            });
            
            socket.on('disconnect', () => {
                updateServerStatus(false);
            });
            
            socket.on('files_update', function(data) {
                        socket.on('files_update', function(data) {
            const fileList = document.getElementById('fileList');
            fileList.innerHTML = '<h2>Uploaded Files</h2>';
            
            data.files.forEach(file => {
                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                fileItem.innerHTML = `
                    <div>
                        <strong>${file.name}</strong>
                        <br>
                        <small>${file.size_readable}</small>
                    </div>
                    <button class="button" onclick="downloadFile('${file.name}')">
                        Download
                    </button>`;
                fileList.appendChild(fileItem);
                    });
                });
            });
            
            socket.on('server_restart', () => {
                showStatus('Server is restarting. Please wait...', false);
                setTimeout(() => {
                    window.location.reload();
                }, 5000);
            });
        }
        
        function updateServerStatus(connected) {
            const indicator = document.getElementById('serverStatus');
            const statusText = document.getElementById('serverStatusText');
            
            indicator.className = 'status-indicator ' + (connected ? 'connected' : 'disconnected');
            statusText.textContent = connected ? 'Connected' : 'Disconnected';
        }
        
        // Settings Management
        function toggleSettings() {
            const modal = document.getElementById('settingsModal');
            modal.style.display = modal.style.display === 'none' ? 'block' : 'none';
            
            if (modal.style.display === 'block') {
                loadCurrentSettings();
            }
        }
        
        async function loadCurrentSettings() {
            try {
                const response = await fetch('/config');
                const config = await response.json();
                
                document.getElementById('hostInput').value = config.host;
                document.getElementById('portInput').value = config.port;
            } catch (error) {
                showStatus('Error loading settings: ' + error.message, true);
            }
        }
        
        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
            });
            
            event.target.classList.add('active');
            document.getElementById(tabName + 'Tab').classList.add('active');
        }
        
        async function saveSettings() {
            const settings = {
                host: document.getElementById('hostInput').value,
                port: document.getElementById('portInput').value
            };
            
            try {
                const response = await fetch('/config', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(settings)
                });
                
                if (response.ok) {
                    showStatus('Settings saved successfully. Server will restart...', false);
                    socket.emit('request_restart');
                } else {
                    throw new Error('Failed to save settings');
                }
            } catch (error) {
                showStatus('Error saving settings: ' + error.message, true);
            }
        }
        
        async function uploadCertificate(file, type) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('type', type);
            
            try {
                const response = await fetch('/upload_cert', {
                    method: 'POST',
                    body: formData
                });
                
                if (response.ok) {
                    showStatus(`${type} uploaded successfully`, false);
                } else {
                    throw new Error(`Failed to upload ${type}`);
                }
            } catch (error) {
                showStatus(`Error uploading ${type}: ${error.message}`, true);
            }
        }
        
        async function generateNewCert() {
            try {
                const response = await fetch('/generate_cert', {
                    method: 'POST'
                });
                
                if (response.ok) {
                    showStatus('New certificate generated successfully. Server will restart...', false);
                    socket.emit('request_restart');
                } else {
                    throw new Error('Failed to generate certificate');
                }
            } catch (error) {
                showStatus('Error generating certificate: ' + error.message, true);
            }
        }
        
    </script>
</body>
</html>
'''

# Routes
@app.route('/config', methods=['GET', 'POST'])
def manage_config():
    if request.method == 'POST':
        data = request.get_json()
        config['Server'].update(data)
        save_config(config)
        return jsonify({'message': 'Configuration updated successfully'})
    return jsonify(dict(config['Server']))

# Force HTTPS
@app.before_request
def before_request():
    if not request.is_secure:
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url)

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

class FileHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        if event.is_directory:
            return
        
        files = []
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            size = os.path.getsize(filepath)
            files.append({
                'name': filename,
                'size': size,
                'size_readable': convert_size(size)
            })
        socketio.emit('files_update', {'files': files})

@app.route('/upload_cert', methods=['POST'])
def upload_certificate():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    cert_type = request.form.get('type', '')
    
    if cert_type not in ['certificate', 'private_key']:
        return jsonify({'error': 'Invalid certificate type'}), 400
    
    filename = config['Server']['cert_path'] if cert_type == 'certificate' else config['Server']['key_path']
    
    try:
        file.save(filename)
        return jsonify({'message': f'{cert_type} uploaded successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate_cert', methods=['POST'])
def generate_new_certificate():
    try:
        generate_ssl_certificate(
            config['Server']['cert_path'],
            config['Server']['key_path']
        )
        return jsonify({'message': 'Certificate generated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@socketio.on('request_restart')
def handle_restart():
    socketio.emit('server_restart')
    threading.Thread(target=restart_server).start()

def restart_server():
    time.sleep(5)  # Give clients time to receive the restart notification
    os._exit(0)  # Force restart by exiting

@app.route('/files', methods=['GET'])
def list_files():
    files = []
    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        size = os.path.getsize(filepath)
        files.append({
            'name': filename,
            'size': size,
            'size_readable': convert_size(size)
        })
    return jsonify(files)

@app.route('/download/<filename>', methods=['GET'])
def download_file(filename):
    try:
        return send_file(
            os.path.join(app.config['UPLOAD_FOLDER'], filename),
            as_attachment=True
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 404

def convert_size(size_bytes):
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

if __name__ == '__main__':
    cert_path = 'certificate.pem'
    key_path = 'private_key.pem'
    
    # Check if certificate files exist, if not create them
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        print("Generating SSL certificate...")
        os.system(f'openssl req -x509 -newkey rsa:4096 -nodes -out {cert_path} '
                 f'-keyout {key_path} -days 365 -subj "/CN=localhost"')
        print("SSL certificate generated.")
    
    # Create SSL context
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(cert_path, key_path)
    
    # Configure SSL context for security
    ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_context.set_ciphers('ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384')
    ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    ssl_context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
    
    # Start the Flask application with SSL
    print("Starting secure file transfer server...")
    app.run(
        host='0.0.0.0',
        port=443,
        ssl_context=ssl_context,
        debug=False,
        threaded=True
    )

    parser = argparse.ArgumentParser(description='Secure File Transfer Server')
    parser.add_argument('--host', help='Host address')
    parser.add_argument('--port', type=int, help='Port number')
    parser.add_argument('--cert', help='Path to SSL certificate')
    parser.add_argument('--key', help='Path to SSL private key')
    args = parser.parse_args()
    
    if args.host:
        config['Server']['host'] = args.host
    if args.port:
        config['Server']['port'] = str(args.port)
    if args.cert:
        config['Server']['cert_path'] = args.cert
    if args.key:
        config['Server']['key_path'] = args.key
    
    save_config(config)
    observer = start_file_monitor()
    
    try:
        ssl_context = setup_ssl_context(
            config['Server']['cert_path'],
            config['Server']['key_path']
        )
        
        print(f"Starting secure file transfer server on {config['Server']['host']}:{config['Server']['port']}...")
        socketio.run(
            app,
            host=config['Server']['host'],
            port=int(config['Server']['port']),
            ssl_context=ssl_context,
            debug=False
        )
    finally:
        observer.stop()
        observer.join()
    pass

#=========<Code End>==========#

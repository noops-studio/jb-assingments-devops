from flask import Flask, render_template_string, jsonify
from flask_socketio import SocketIO, emit
import psutil
import os
import socket
import threading
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'webapp-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

instance_id = os.environ.get('INSTANCE_ID', 'unknown')
hostname = socket.gethostname()

def get_system_metrics():
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    return {
        'instance_id': instance_id,
        'hostname': hostname,
        'cpu_percent': cpu_percent,
        'memory_percent': memory.percent,
        'memory_used_gb': round(memory.used / (1024**3), 2),
        'memory_total_gb': round(memory.total / (1024**3), 2),
        'disk_percent': disk.percent,
        'disk_used_gb': round(disk.used / (1024**3), 2),
        'disk_total_gb': round(disk.total / (1024**3), 2)
    }

@app.route('/')
def index():
    html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Server Metrics Dashboard</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        .server-info {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
            color: white;
            text-align: center;
            font-size: 1.2em;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .metric-card {
            background: rgba(255,255,255,0.95);
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.1);
            transition: transform 0.3s ease;
        }
        .metric-card:hover {
            transform: translateY(-5px);
        }
        .metric-title {
            font-size: 1.1em;
            color: #666;
            margin-bottom: 15px;
            font-weight: 600;
        }
        .metric-value {
            font-size: 2.5em;
            font-weight: bold;
            color: #667eea;
            margin-bottom: 10px;
        }
        .metric-detail {
            font-size: 0.9em;
            color: #888;
        }
        .progress-bar {
            width: 100%;
            height: 25px;
            background: #e0e0e0;
            border-radius: 12px;
            overflow: hidden;
            margin-top: 15px;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            transition: width 0.5s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 0.9em;
        }
        .status-indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #4caf50;
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .footer {
            text-align: center;
            color: white;
            margin-top: 30px;
            opacity: 0.8;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Server Metrics Dashboard</h1>
            <div class="server-info">
                <span class="status-indicator"></span>
                <strong>Server:</strong> <span id="hostname">Loading...</span> | 
                <strong>Instance ID:</strong> <span id="instance-id">Loading...</span>
            </div>
        </div>
        
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-title">CPU Usage</div>
                <div class="metric-value" id="cpu-value">0%</div>
                <div class="progress-bar">
                    <div class="progress-fill" id="cpu-progress" style="width: 0%">0%</div>
                </div>
            </div>
            
            <div class="metric-card">
                <div class="metric-title">Memory Usage</div>
                <div class="metric-value" id="memory-value">0%</div>
                <div class="metric-detail" id="memory-detail">0 GB / 0 GB</div>
                <div class="progress-bar">
                    <div class="progress-fill" id="memory-progress" style="width: 0%">0%</div>
                </div>
            </div>
            
            <div class="metric-card">
                <div class="metric-title">Disk Usage</div>
                <div class="metric-value" id="disk-value">0%</div>
                <div class="metric-detail" id="disk-detail">0 GB / 0 GB</div>
                <div class="progress-bar">
                    <div class="progress-fill" id="disk-progress" style="width: 0%">0%</div>
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p>Live metrics updated every second via WebSocket</p>
        </div>
    </div>

    <script>
        const socket = io();
        
        socket.on('connect', function() {
            console.log('Connected to server');
        });
        
        socket.on('metrics', function(data) {
            document.getElementById('hostname').textContent = data.hostname;
            document.getElementById('instance-id').textContent = data.instance_id;
            
            document.getElementById('cpu-value').textContent = data.cpu_percent.toFixed(1) + '%';
            document.getElementById('cpu-progress').style.width = data.cpu_percent + '%';
            document.getElementById('cpu-progress').textContent = data.cpu_percent.toFixed(1) + '%';
            
            document.getElementById('memory-value').textContent = data.memory_percent.toFixed(1) + '%';
            document.getElementById('memory-detail').textContent = 
                data.memory_used_gb + ' GB / ' + data.memory_total_gb + ' GB';
            document.getElementById('memory-progress').style.width = data.memory_percent + '%';
            document.getElementById('memory-progress').textContent = data.memory_percent.toFixed(1) + '%';
            
            document.getElementById('disk-value').textContent = data.disk_percent.toFixed(1) + '%';
            document.getElementById('disk-detail').textContent = 
                data.disk_used_gb + ' GB / ' + data.disk_total_gb + ' GB';
            document.getElementById('disk-progress').style.width = data.disk_percent + '%';
            document.getElementById('disk-progress').textContent = data.disk_percent.toFixed(1) + '%';
        });
        
        socket.on('disconnect', function() {
            console.log('Disconnected from server');
        });
    </script>
</body>
</html>
"""
    return render_template_string(html_template)

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/api/metrics')
def api_metrics():
    return jsonify(get_system_metrics())

def emit_metrics():
    while True:
        metrics = get_system_metrics()
        socketio.emit('metrics', metrics)
        time.sleep(1)

@socketio.on('connect')
def handle_connect():
    emit('metrics', get_system_metrics())

if __name__ == '__main__':
    metrics_thread = threading.Thread(target=emit_metrics, daemon=True)
    metrics_thread.start()
    socketio.run(app, host='0.0.0.0', port=80, allow_unsafe_werkzeug=True)

#!/usr/bin/env python3
import socket
import psutil
import os
import threading
import time
import math
import subprocess
from flask import Flask, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', logger=False, engineio_logger=False)

stress_process = None
stress_running = False
stress_lock = threading.Lock()

def get_color_class(value):
    if value < 50:
        return 'success'
    elif value < 80:
        return 'warning'
    else:
        return 'danger'

def get_server_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "unknown"

def get_metrics():
    hostname = socket.gethostname()
    server_ip = get_server_ip()
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    memory_percent = memory.percent
    memory_total_gb = memory.total / (1024 ** 3)
    memory_used_gb = memory.used / (1024 ** 3)
    memory_free_gb = memory.free / (1024 ** 3)
    disk = psutil.disk_usage('/')
    disk_total_gb = disk.total / (1024 ** 3)
    disk_used_gb = disk.used / (1024 ** 3)
    disk_free_gb = disk.free / (1024 ** 3)
    disk_percent = (disk.used / disk.total) * 100
    
    with stress_lock:
        stress_status = stress_running
    
    return {
        'hostname': hostname,
        'server_ip': server_ip,
        'stress_running': stress_status,
        'cpu': {
            'percent': cpu_percent,
            'color': get_color_class(cpu_percent)
        },
        'memory': {
            'percent': memory_percent,
            'total_gb': memory_total_gb,
            'used_gb': memory_used_gb,
            'free_gb': memory_free_gb,
            'color': get_color_class(memory_percent)
        },
        'disk': {
            'percent': disk_percent,
            'total_gb': disk_total_gb,
            'used_gb': disk_used_gb,
            'free_gb': disk_free_gb,
            'color': get_color_class(disk_percent)
        }
    }

def start_stress():
    global stress_process, stress_running
    with stress_lock:
        if stress_running:
            return False
        
        cpu_count = psutil.cpu_count()
        workers = max(2, int(cpu_count * 0.6))
        
        try:
            proc = subprocess.Popen(
                ['stress-ng', '--cpu', str(workers), '--timeout', '0'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
            )
            stress_process = proc
            stress_running = True
            return True
        except Exception as e:
            print(f"Error starting stress-ng: {e}")
            return False

def stop_stress():
    global stress_process, stress_running
    with stress_lock:
        stress_running = False
        
        if stress_process:
            try:
                os.killpg(os.getpgid(stress_process.pid), 15)
            except:
                try:
                    stress_process.terminate()
                except:
                    pass
            stress_process = None
        
        for _ in range(3):
            try:
                result = subprocess.run(['pkill', '-9', 'stress-ng'], 
                                     stdout=subprocess.DEVNULL, 
                                     stderr=subprocess.DEVNULL,
                                     timeout=1)
                if result.returncode != 0:
                    break
                time.sleep(0.3)
            except:
                break

def background_thread():
    while True:
        metrics = get_metrics()
        socketio.emit('metrics_update', metrics, namespace='/')
        time.sleep(2)

@app.route('/health')
def health():
    return '', 200

@app.route('/stress/start')
def stress_start():
    if start_stress():
        return jsonify({'status': 'started', 'message': 'CPU stress started'}), 200
    return jsonify({'status': 'already_running', 'message': 'CPU stress already running'}), 200

@app.route('/stress/stop')
def stress_stop():
    stop_stress()
    return jsonify({'status': 'stopped', 'message': 'CPU stress stopped'}), 200

@app.route('/stress/status')
def stress_status():
    with stress_lock:
        running = stress_running
    return jsonify({'running': running}), 200

@app.route('/')
def index():
    metrics = get_metrics()
    hostname = metrics['hostname']
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>System Metrics - {hostname}</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
        }}
        
        .container {{
            max-width: 1200px;
            width: 100%;
        }}
        
        .header {{
            text-align: center;
            color: white;
            margin-bottom: 40px;
            animation: fadeInDown 0.6s ease-out;
        }}
        
        .header h1 {{
            font-size: 3rem;
            font-weight: 700;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}
        
        .header .hostname {{
            font-size: 1.5rem;
            opacity: 0.9;
            font-weight: 300;
        }}
        
        .server-info {{
            background: rgba(255, 255, 255, 0.2);
            padding: 15px 25px;
            border-radius: 15px;
            margin-top: 15px;
            display: inline-block;
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
        }}
        
        .server-info.changed {{
            animation: serverChange 1s ease;
            background: rgba(76, 175, 80, 0.3);
        }}
        
        @keyframes serverChange {{
            0% {{ transform: scale(1); background: rgba(255, 255, 255, 0.2); }}
            50% {{ transform: scale(1.05); background: rgba(76, 175, 80, 0.5); }}
            100% {{ transform: scale(1); background: rgba(255, 255, 255, 0.2); }}
        }}
        
        .server-label {{
            font-size: 0.9rem;
            opacity: 0.8;
            margin-bottom: 5px;
        }}
        
        .server-value {{
            font-size: 1.3rem;
            font-weight: 600;
            font-family: 'Courier New', monospace;
        }}
        
        .status-indicator {{
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-left: 10px;
            animation: pulse 2s infinite;
        }}
        
        .status-indicator.connected {{
            background: #4caf50;
            box-shadow: 0 0 10px rgba(76, 175, 80, 0.5);
        }}
        
        .status-indicator.disconnected {{
            background: #f44336;
            box-shadow: 0 0 10px rgba(244, 67, 54, 0.5);
        }}
        
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
        
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 25px;
            animation: fadeInUp 0.8s ease-out;
        }}
        
        .metric-card {{
            background: white;
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            position: relative;
            overflow: hidden;
        }}
        
        .metric-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 5px;
            background: linear-gradient(90deg, #667eea, #764ba2);
        }}
        
        .metric-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 15px 50px rgba(0,0,0,0.3);
        }}
        
        .metric-card.updating {{
            animation: cardPulse 0.5s ease;
        }}
        
        @keyframes cardPulse {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.02); }}
        }}
        
        .metric-header {{
            display: flex;
            align-items: center;
            margin-bottom: 20px;
        }}
        
        .metric-icon {{
            font-size: 2.5rem;
            margin-right: 15px;
            width: 60px;
            height: 60px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}
        
        .metric-title {{
            font-size: 1.2rem;
            color: #333;
            font-weight: 600;
        }}
        
        .metric-value {{
            font-size: 3rem;
            font-weight: 700;
            color: #667eea;
            margin: 15px 0;
            transition: all 0.3s ease;
        }}
        
        .metric-details {{
            color: #666;
            font-size: 0.9rem;
            margin-top: 10px;
            transition: all 0.3s ease;
        }}
        
        .progress-bar {{
            width: 100%;
            height: 12px;
            background: #e0e0e0;
            border-radius: 10px;
            overflow: hidden;
            margin-top: 15px;
            position: relative;
        }}
        
        .progress-fill {{
            height: 100%;
            border-radius: 10px;
            transition: width 0.5s ease, background 0.3s ease;
            position: relative;
        }}
        
        .progress-fill.success {{
            background: linear-gradient(90deg, #4caf50, #66bb6a);
        }}
        
        .progress-fill.warning {{
            background: linear-gradient(90deg, #ff9800, #ffb74d);
        }}
        
        .progress-fill.danger {{
            background: linear-gradient(90deg, #f44336, #ef5350);
        }}
        
        .progress-fill::after {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
            animation: shimmer 2s infinite;
        }}
        
        @keyframes shimmer {{
            0% {{ transform: translateX(-100%); }}
            100% {{ transform: translateX(100%); }}
        }}
        
        @keyframes fadeInDown {{
            from {{
                opacity: 0;
                transform: translateY(-30px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        @keyframes fadeInUp {{
            from {{
                opacity: 0;
                transform: translateY(30px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        .footer {{
            text-align: center;
            color: white;
            margin-top: 40px;
            opacity: 0.8;
            font-size: 0.9rem;
        }}
        
        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 2rem;
            }}
            
            .metric-card {{
                padding: 20px;
            }}
            
            .metric-value {{
                font-size: 2rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 System Metrics <span class="status-indicator disconnected" id="statusIndicator"></span></h1>
            <div class="hostname" id="hostname">{hostname}</div>
            <div class="server-info" id="serverInfo">
                <div class="server-label">Connected to Server:</div>
                <div class="server-value" id="serverIp">{metrics.get('server_ip', 'Loading...')}</div>
            </div>
        </div>
        
        <div class="metrics-grid">
            <div class="metric-card" id="cpu-card">
                <div class="metric-header">
                    <div class="metric-icon">⚡</div>
                    <div class="metric-title">CPU Usage</div>
                </div>
                <div class="metric-value" id="cpu-value">{metrics['cpu']['percent']:.1f}%</div>
                <div class="progress-bar">
                    <div class="progress-fill" id="cpu-progress" style="width: {metrics['cpu']['percent']}%"></div>
                </div>
            </div>
            
            <div class="metric-card" id="memory-card">
                <div class="metric-header">
                    <div class="metric-icon">💾</div>
                    <div class="metric-title">Memory Usage</div>
                </div>
                <div class="metric-value" id="memory-value">{metrics['memory']['percent']:.1f}%</div>
                <div class="metric-details" id="memory-details">
                    {metrics['memory']['used_gb']:.2f} GB / {metrics['memory']['total_gb']:.2f} GB used<br>
                    {metrics['memory']['free_gb']:.2f} GB free
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="memory-progress" style="width: {metrics['memory']['percent']}%"></div>
                </div>
            </div>
            
            <div class="metric-card" id="disk-card">
                <div class="metric-header">
                    <div class="metric-icon">💿</div>
                    <div class="metric-title">Disk Usage</div>
                </div>
                <div class="metric-value" id="disk-value">{metrics['disk']['percent']:.1f}%</div>
                <div class="metric-details" id="disk-details">
                    {metrics['disk']['used_gb']:.2f} GB / {metrics['disk']['total_gb']:.2f} GB used<br>
                    {metrics['disk']['free_gb']:.2f} GB free
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="disk-progress" style="width: {metrics['disk']['percent']}%"></div>
                </div>
            </div>
        </div>
        
        <div class="footer">
            <div style="margin-bottom: 15px;">
                <button id="stressBtn" onclick="toggleStress()" style="
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    border: none;
                    padding: 12px 24px;
                    border-radius: 25px;
                    font-size: 1rem;
                    font-weight: 600;
                    cursor: pointer;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.2);
                    transition: transform 0.2s;
                " onmouseover="this.style.transform='scale(1.05)'" onmouseout="this.style.transform='scale(1)'">
                    <span id="stressBtnText">Start CPU Stress</span>
                </button>
            </div>
            Real-time system metrics dashboard • Live updates via WebSocket
        </div>
    </div>
    
    <script>
        const socket = io('/', {{ transports: ['websocket', 'polling'] }});
        const statusIndicator = document.getElementById('statusIndicator');
        const serverInfo = document.getElementById('serverInfo');
        const serverIp = document.getElementById('serverIp');
        let stressRunning = false;
        let lastServerIp = null;
        
        socket.on('connect', function() {{
            statusIndicator.classList.remove('disconnected');
            statusIndicator.classList.add('connected');
        }});
        
        socket.on('disconnect', function() {{
            statusIndicator.classList.remove('connected');
            statusIndicator.classList.add('disconnected');
        }});
        
        socket.on('connect_error', function() {{
            console.log('Connection error');
        }});
        
        socket.on('metrics_update', function(data) {{
            updateMetric('cpu', data.cpu);
            updateMetric('memory', data.memory, true);
            updateMetric('disk', data.disk, true);
            
            document.getElementById('hostname').textContent = data.hostname;
            
            if (data.server_ip) {{
                const currentIp = data.server_ip;
                if (lastServerIp && lastServerIp !== currentIp) {{
                    serverInfo.classList.add('changed');
                    setTimeout(() => {{
                        serverInfo.classList.remove('changed');
                    }}, 1000);
                }}
                serverIp.textContent = currentIp;
                lastServerIp = currentIp;
            }}
            
            if (data.stress_running !== undefined) {{
                stressRunning = data.stress_running;
                updateStressButton();
            }}
        }});
        
        socket.on('stress_status', function(data) {{
            stressRunning = data.running;
            updateStressButton();
        }});
        
        function updateStressButton() {{
            const btn = document.getElementById('stressBtn');
            const btnText = document.getElementById('stressBtnText');
            if (stressRunning) {{
                btnText.textContent = 'Stop CPU Stress';
                btn.style.background = 'linear-gradient(135deg, #f44336 0%, #ef5350 100%)';
            }} else {{
                btnText.textContent = 'Start CPU Stress';
                btn.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
            }}
        }}
        
        function toggleStress() {{
            if (stressRunning) {{
                socket.emit('stress_stop');
            }} else {{
                socket.emit('stress_start');
            }}
        }}
        
        socket.emit('stress_status');
        
        function updateMetric(metricName, metricData, hasDetails = false) {{
            const card = document.getElementById(metricName + '-card');
            const valueEl = document.getElementById(metricName + '-value');
            const progressEl = document.getElementById(metricName + '-progress');
            
            card.classList.add('updating');
            setTimeout(() => card.classList.remove('updating'), 500);
            
            valueEl.textContent = metricData.percent.toFixed(1) + '%';
            progressEl.style.width = metricData.percent + '%';
            progressEl.className = 'progress-fill ' + metricData.color;
            
            if (hasDetails) {{
                const detailsEl = document.getElementById(metricName + '-details');
                if (metricName === 'memory') {{
                    detailsEl.innerHTML = `
                        ${{metricData.used_gb.toFixed(2)}} GB / ${{metricData.total_gb.toFixed(2)}} GB used<br>
                        ${{metricData.free_gb.toFixed(2)}} GB free
                    `;
                }} else if (metricName === 'disk') {{
                    detailsEl.innerHTML = `
                        ${{metricData.used_gb.toFixed(2)}} GB / ${{metricData.total_gb.toFixed(2)}} GB used<br>
                        ${{metricData.free_gb.toFixed(2)}} GB free
                    `;
                }}
            }}
        }}
    </script>
</body>
</html>"""
    
    return html, 200

@socketio.on('connect', namespace='/')
def handle_connect():
    emit('metrics_update', get_metrics())

@socketio.on('stress_start', namespace='/')
def handle_stress_start():
    result = start_stress()
    with stress_lock:
        status = stress_running
    emit('stress_status', {'running': status, 'success': result})

@socketio.on('stress_stop', namespace='/')
def handle_stress_stop():
    stop_stress()
    with stress_lock:
        status = stress_running
    emit('stress_status', {'running': status, 'success': True})

@socketio.on('stress_status', namespace='/')
def handle_stress_status():
    with stress_lock:
        status = stress_running
    emit('stress_status', {'running': status})

if __name__ == '__main__':
    socketio.start_background_task(background_thread)
    socketio.run(app, host='0.0.0.0', port=80, allow_unsafe_werkzeug=True)

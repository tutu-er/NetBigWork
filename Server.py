import threading
import socket
import json
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

# --- 配置 ---
HOST_IP = '0.0.0.0'       # 监听所有网络接口
TCP_PORT = 9000           # 必须与 STM32 的 SERVER_PORT 一致
WEB_PORT = 5000           # Flask 网页端口

# --- 全局状态 ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key' # 用于 Flask-SocketIO
socketio = SocketIO(app)

latest_data = {
    "DC": 0.0,
    "Amp": 0.0,
    "Freq": 0.0,
    "time": 0,
    "status": "等待设备连接..."
}

stm32_client_socket = None # 用于存储当前连接的 STM32 客户端 socket

# --- TCP Server 线程函数 (用于接收 STM32 数据) ---

def tcp_server_thread():
    global stm32_client_socket, latest_data

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST_IP, TCP_PORT))
            s.listen(1)
            print(f"TCP 服务器已启动，监听在端口 {TCP_PORT}...")
            latest_data["status"] = "等待设备连接..."
            socketio.emit('update_data', latest_data) # 推送状态到网页
        except Exception as e:
            print(f"!!! TCP 服务器启动失败: {e}")
            latest_data["status"] = f"启动失败: {e}"
            socketio.emit('update_data', latest_data)
            return

        while True:
            conn, addr = s.accept()
            stm32_client_socket = conn # 存储当前连接的 socket
            print(f"\n✅ STM32 客户端已连接: {addr}")
            latest_data["status"] = f"设备已连接: {addr[0]}"
            socketio.emit('update_data', latest_data)

            with conn:
                while True:
                    try:
                        # 接收数据 (STM32 使用 AT+CIPSEND 发送)
                        # 假设 STM32 发送的数据以 JSON 格式和 '\r\n' 结尾
                        data = conn.recv(1024).decode('utf-8')
                        if not data:
                            break # 连接断开

                        # 简单处理，假设接收到的是完整的 JSON 字符串
                        json_str = data.strip()
                        print(f"  ▶️ 收到数据: {json_str}")

                        try:
                            # 尝试解析 JSON
                            received_json = json.loads(json_str)

                            # 更新全局状态
                            latest_data.update(received_json)
                            latest_data["status"] = "数据正常接收"
                            
                            # 推送新数据到所有连接的 Web 客户端 (使用 SocketIO)
                            socketio.emit('update_data', latest_data)
                            
                        except json.JSONDecodeError as e:
                            print(f"!!! JSON 解析错误: {e} | 原始数据: {json_str}")
                            
                    except ConnectionResetError:
                        break # 客户端强制关闭
                    except Exception as e:
                        print(f"!!! TCP 通信错误: {e}")
                        break
            
            # 连接断开
            print(f"❌ STM32 客户端连接已关闭: {addr}")
            stm32_client_socket = None
            latest_data["status"] = "设备已断开连接"
            socketio.emit('update_data', latest_data)

# --- Web 路由 (用于网页展示和命令下发) ---

@app.route('/')
def index():
    """渲染主页面"""
    return render_template('index.html', initial_data=latest_data)

@app.route('/send_command', methods=['POST'])
def send_command():
    """处理来自 Web 页面的命令请求"""
    global stm32_client_socket
    
    # 1. 检查连接状态
    if stm32_client_socket is None:
        return json.dumps({'status': 'error', 'message': 'STM32 客户端未连接'}), 400

    # 2. 获取命令参数
    data = request.json
    rate = data.get('rate', 0)
    cycles = data.get('cycles', 0)
    
    # 3. 构建发送给 STM32 的 JSON 命令
    command_data = json.dumps({
        "cmd": "config", 
        "rate": int(rate), 
        "cycles": int(cycles)
    })
    
    # 4. 构建 AT+CIPSEND 指令
    command_len = len(command_data)
    
    # 【注意】ESP 模块期望先收到 CIPSEND 命令，再收到数据体。
    # 由于 Python 服务器和 ESP 模块之间的通信逻辑复杂，
    # 且 STM32 的代码是基于异步接收 IPD 的，
    # 最简单的做法是直接将命令数据发送给 STM32 (它会作为 +IPD 接收)
    # STM32 代码中的 AT_CheckForClientData 已经实现了 +IPD 数据的解析。

    # 直接将 JSON 数据发送给 STM32 客户端
    # 因为 STM32 代码中的 `AT_CheckForClientData` 是异步监听 `+IPD` 数据。
    # 在 ESP8266/ESP32 处于 Client 模式并连接到 Server 时，
    # Server 发送的任何数据都会以 `+IPD,<len>:<data>` 的格式异步推送到 STM32 的 UART。
    
    try:
        # 发送数据
        stm32_client_socket.sendall(command_data.encode('utf-8'))
        print(f"  ◀️ 发送命令成功: {command_data}")
        return json.dumps({'status': 'success', 'message': '命令发送成功'}), 200
    except Exception as e:
        print(f"!!! 命令发送失败: {e}")
        return json.dumps({'status': 'error', 'message': f'命令发送失败: {e}'}), 500

# --- SocketIO 事件 (用于 WebSocket 连接) ---

@socketio.on('connect')
def test_connect():
    """新的 Web 客户端连接时发送当前最新数据"""
    print('Web 客户端已连接')
    emit('update_data', latest_data)

# --- 主程序启动 ---

if __name__ == '__main__':
    # 启动 TCP 服务器线程
    tcp_thread = threading.Thread(target=tcp_server_thread)
    tcp_thread.daemon = True # 允许主程序退出时线程也退出
    tcp_thread.start()

    # 启动 Flask Web 服务器
    # 使用 socketio.run 来启动，以便同时运行 Flask 和 WebSocket 服务器
    print(f"\n🌐 Web 服务器正在启动，访问 http://127.0.0.1:{WEB_PORT}")
    socketio.run(app, host=HOST_IP, port=WEB_PORT, allow_unsafe_werkzeug=True)
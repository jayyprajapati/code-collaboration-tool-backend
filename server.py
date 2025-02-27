import os
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from dotenv import load_dotenv
from datetime import datetime
import hashlib
from flask import request as flask_request
from flask_cors import CORS
from code_executor import execute_code, get_run_command
import docker
import base64

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
socketio = SocketIO(app, cors_allowed_origins="*")

# def validate_session_ownership(session_id, socket_id):
#     session = sessions.get(session_id)
#     if not session:
#         return False
#     user = session["users"].get(socket_id)
#     return user and user["role"] == "owner"

# Temporary session storage (we'll replace with DB later)
sessions = {}

def session_exists(session_id):
    return session_id in sessions

# Session Management
def create_session(session_id, password, owner):
    sessions[session_id] = {
        "users": {},
        "code": "// New session started...",
        "chat": [],
        "password": hashlib.sha256(password.encode()).hexdigest(),
        "owner": owner,
        "created_at": datetime.now(),
        "active": True
    }

def verify_session(session_id, password):
    session = sessions.get(session_id)
    
    if not session or not session["active"]:
        return False

    return session['password'] == hashlib.sha256(password.encode()).hexdigest()

@socketio.on("connect")
def handle_connect():
    print("Client connected:", request.sid)

@socketio.on("join-session")
def handle_join_session(data):
    try:
        session_id = data["sessionId"]
        password = data["password"]
        user = data["user"]
        user_id = data["userId"]

        if not all([session_id, password, user, user_id]):
            emit("error", {"message": "Missing required fields"})
            return
        
        # Verify password logic here
        if not verify_session(session_id, password):
            emit("error", {"message": "Invalid password"})
            return
        
        session = sessions[session_id]

        if not session:
            emit("error", {"message": "Session does not exist"})
            return

        is_owner = user_id == session["owner"]

        session["users"][request.sid] = {
            "name": user,
            "id": user_id,
            "role": "owner" if is_owner else "editor"
        }

        # Create session if it doesn't exist
        # if session_id not in sessions:
        #     sessions[session_id] = {
        #         "users": {},
        #         "code": "// New session created",
        #         "chat": [],
        #         "owner": user
        #     }
    
        join_room(session_id)
        user_list = [{"name": u["name"], "role": u["role"]} 
                    for u in sessions[session_id]["users"].values()]
        emit("user-list", user_list, room=session_id)  # Add this line
        emit("user-joined", {"user": data["user"]}, room=session_id)
        
        # Send existing code to new user
        emit("code-update", session["code"], to=request.sid)

        emit("session-data", {
            "code": session["code"],
            "chat": session["chat"],
            "role": session["users"][request.sid]["role"]
        })
        print(f"user {user} joined the session {session_id}")

    except Exception as e:
        print(f"Join session error: {str(e)}")
        emit("error", {"message": "Internal server error"})

@socketio.on("end-session")
def handle_end_session(data):
    session_id = data["sessionId"]
    user_id = data["userId"]
    
    if sessions.get(session_id, {}).get("owner") == user_id:
        sessions[session_id]["active"] = False
        emit("session-ended", room=session_id)
        del sessions[session_id]

@socketio.on("leave-session")
def handle_leave_session(session_id):
    if session_id in sessions:
        user = sessions[session_id]["users"].get(request.sid, {}).get("name")
        if user:
            leave_room(session_id)
            del sessions[session_id]["users"][request.sid]
            emit("user-left", {"user": user, "message": f"{user} has left the session"}, room=session_id)

@socketio.on("code-change")
def handle_code_change(data):
    session_id = data["sessionId"]
    code = data["code"]
    sender_sid = request.sid
    
    if session_id in sessions:
        sessions[session_id]["code"] = code
        emit("code-update", code, room=session_id, skip_sid=sender_sid)

@socketio.on("send-chat-message")
def handle_chat_message(data):
    try:
        session_id = data["sessionId"]
        message = data["message"]
        
        if session_id not in sessions:
            return
            
        user_info = sessions[session_id]["users"].get(request.sid)
        if not user_info:
            return
            
        chat_message = {
            "user": user_info["name"],
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        
        sessions[session_id]["chat"].append(chat_message)
        emit("chat-message", chat_message, room=session_id)
        
    except KeyError as e:
        print(f"Chat message error: {str(e)}")

@socketio.on("disconnect")
def handle_disconnect():
    for session_id, session in sessions.items():
        if request.sid in session["users"]:
            user = session["users"][request.sid]["name"]
            leave_room(session_id)
            emit("user-left", {
                "user": user,
                "message": f"{user} has left the session"
            }, room=session_id)
            del session["users"][request.sid]
            
            # Clean up empty sessions
            if not session["users"]:
                del sessions[session_id]
            break

@socketio.on("change-role")
def handle_role_change(data):
    session_id = data["sessionId"]
    target_user = data["targetUser"]
    new_role = data["newRole"]
    requester_sid = request.sid

    if session_id in sessions:
        requester_role = sessions[session_id]["users"].get(requester_sid, {}).get("role")
        
        if requester_role == "owner":
            for sid, user in sessions[session_id]["users"].items():
                if user["name"] == target_user:
                    user["role"] = new_role
                    emit("role-updated", {
                        "user": target_user,
                        "newRole": new_role
                    }, room=session_id)
                    break

MAX_OUTPUT_LENGTH = 1000
client = docker.from_env()
active_containers = {}

@socketio.on('run-code')
def handle_run_code(data):
    session_id = data['sessionId']
    code = data['code']
    language = data['language']
    
    encoded_code = base64.urlsafe_b64encode(code.encode()).decode()
    try:
        # 1. Determine image and command based on language
        # image, command = {
        #     'python': (
        #         'python:3.9',
        #         f'sh -c "echo \'{encoded_code}\' > code.py && python code.py"'
        #     ),
        #     'javascript': (
        #         'node:16',
        #         f'sh -c "echo \'{encoded_code}\' > code.js && node code.js"'
        #     )
        # }[language]
        if language == 'python':
            image = 'python:3.9'
            file_ext = 'py'
            mem_limit = '100m'
            run_cmd = 'python -u /app/code.py'
        elif language == 'javascript':
            image = 'node:16'
            file_ext = 'js'
            mem_limit = '100m'
            run_cmd = 'node /app/code.js'
        elif language == 'java':
            image = 'openjdk:17'
            file_ext = 'java'
            run_cmd = 'javac /app/code.java && java -cp /app code'
            mem_limit = '512m'
        else:
            raise KeyError(f"Unsupported language: {language}")
        # 2. Create and run container with required arguments
        container = client.containers.run(
            image=image,          # ← REQUIRED
            command=[
                'sh', '-c',
                f'mkdir -p /app && '
                f'echo "{encoded_code}" | base64 -d > /app/code.{file_ext} && '
                f'{run_cmd}'
            ],      # ← REQUIRED
            detach=True,
            mem_limit=mem_limit,
            network_mode='none',
            stdout=True,
            stderr=True
        )

        # 3. Stream logs
        for line in container.logs(stream=True, follow=True):
            emit('terminal-output', {
                'sessionId': session_id,
                'output': line.decode()
            }, room=session_id)

        # 4. Cleanup
        container.remove(force=True)
        emit('execution-complete', {'sessionId': session_id}, room=session_id)

    except KeyError:
        emit('terminal-output', {
            'sessionId': session_id,
            'output': f"Unsupported language: {language}\n"
        }, room=session_id)
    except Exception as e:
        emit('terminal-output', {
            'sessionId': session_id,
            'output': f"Execution error: {str(e)}\n"
        }, room=session_id)


@socketio.on("code-executed")
def handle_code_execution(data):
    # if len(data.get("output", "")) > MAX_OUTPUT_LENGTH:
    #     emit("error", {"message": "Output too large"})
    #     return
        
    # if not validate_session_ownership(data["sessionId"], request.sid):
    #     emit("error", {"message": "Unauthorized"})
    #     return
    try:
        # Validate session and user permissions
        if data["sessionId"] not in sessions:
            return

        user = sessions[data["sessionId"]]["users"].get(request.sid)
        if not user or user["role"] not in ["owner", "editor"]:
            return

        # Limit output size
        output = data["output"][:1000]  # Truncate to 1000 chars
        
        emit("execution-result", {
            "output": output,
            "user": user["name"],
            "timestamp": datetime.now().isoformat()
        }, room=data["sessionId"])
        
    except KeyError as e:
        print(f"Execution broadcast error: {str(e)}")

@app.route('/api/verify-session', methods=['POST'])
def api_verify_session():
    data = flask_request.get_json()
    session_id = data.get('sessionId')
    password = data.get('password')
    
    if not session_exists(session_id):
        return {'valid': False, 'error': 'Session does not exist'}, 404
    
    if not verify_session(session_id, password):
        return {'valid': False, 'error': 'Invalid password'}, 401
    
    return {'valid': True}, 200

@app.route('/api/create-session', methods=['POST'])
def api_create_session():
    data = flask_request.get_json()
    session_id = data.get('sessionId')
    password = data.get('password')
    owner = data.get('owner')
    
    if session_exists(session_id):
        return {'valid': False, 'error': 'Session exists with same sessionId'}, 400
    
    create_session(session_id, password, owner)
    
    return {'valid': True}, 200

@app.route('/execute', methods=['POST'])
def handle_execute():
    data = request.get_json()
    language = data['language']
    code = data['code']
    input_data = data.get('input', '')
    
    if language == 'javascript':
        # Browser-side execution
        return {'output': 'Use client-side execution for JS'}
    
    # Server-side execution for Python/Java
    result = execute_code(language, code, input_data)
    return jsonify(result)

if __name__ == "__main__":
    socketio.run(app, debug=True, port=3001)
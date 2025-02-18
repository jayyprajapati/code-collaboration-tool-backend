import docker
import time


try:
    client = docker.DockerClient(
    base_url='unix:///var/run/docker.sock',
    timeout=600  # Increase timeout to 10 minutes
)
    client.ping() # Test connection
except docker.errors.DockerException as e:
    print(f"Docker connection failed: {str(e)}")
    print("Make sure Docker Desktop is running!")
    exit(1)

EXECUTION_TIMEOUT = 10  # seconds

def sanitize_code(code):
    blacklist = [
        'os.', 'subprocess', 'sys.', 'open(',
        'exec', 'eval', 'shutil', 'socket'
    ]
    return code if not any(b in code for b in blacklist) else "INVALID_CODE"

def execute_code(language, code, input_data=""):
    images = {
        'python': 'python:slim',
        'java': 'openjdk:17-slim'
    }
    
    try:
        safe_code = sanitize_code(code)
        if "INVALID_CODE" in safe_code:
            return {"error": "Dangerous code detected"}

        # Create temporary container
        container = client.containers.create(
            image=images[language],
            command=["sh", "-c", f"echo '{code}' > code && {get_run_command(language)}"],
            mem_limit='100m',
            network_mode='none',
            security_opt=['no-new-privileges:true'],
            cap_drop=['ALL'],
            tmpfs={'/tmp': 'rw,noexec,nosuid'},
            pids_limit=100,
            read_only=True
        )
        
        # Start and wait with timeout
        container.start()
        start_time = time.time()
        
        while container.status != 'exited':
            if time.time() - start_time > EXECUTION_TIMEOUT:
                container.kill()
                return {"error": "Execution timeout"}
            time.sleep(0.5)
            container.reload()
        
        # Get logs
        logs = container.logs().decode('utf-8')
        container.remove()
        
        return {"output": logs}

    except Exception as e:
        return {"error": str(e)}

def get_run_command(lang):
    return {
        'python': 'python code',
        'java': 'javac code && java Main'
    }[lang]
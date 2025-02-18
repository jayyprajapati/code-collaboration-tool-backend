from flask import request
from flask_limiter.util import get_remote_address

def configure_security(app):
    # Rate limiting
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"]
    )
    
    # Content Security Policy
    @app.after_request
    def add_headers(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'"
        return response
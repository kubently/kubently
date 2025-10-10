"""
Mock OAuth 2.0 / OIDC Provider for Testing

This module provides a mock OAuth provider that simulates the behavior
of a real OIDC provider (like Okta, Auth0, etc.) for testing purposes.
"""

import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


class MockOAuthProvider:
    """
    Mock OAuth 2.0 / OIDC Provider for testing.
    
    Supports:
    - Device Authorization Grant flow
    - Authorization Code flow
    - JWT token generation with RS256 signing
    - JWKS endpoint for public key discovery
    - User info endpoint
    """
    
    def __init__(self, issuer: str = "http://localhost:9000"):
        """Initialize the mock provider."""
        self.issuer = issuer
        self.client_id = "kubently-cli"
        self.client_secret = "mock-secret-123"
        
        # Generate RSA key pair for JWT signing
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        self.public_key = self.private_key.public_key()
        
        # Storage for pending device codes and auth codes
        self.device_codes: Dict[str, dict] = {}
        self.auth_codes: Dict[str, dict] = {}
        self.refresh_tokens: Dict[str, dict] = {}
        
        # Mock users database
        self.users = {
            "test@example.com": {
                "sub": "user-123",
                "email": "test@example.com",
                "name": "Test User",
                "groups": ["developers", "kubently-users"]
            },
            "admin@example.com": {
                "sub": "admin-456",
                "email": "admin@example.com",
                "name": "Admin User",
                "groups": ["admins", "kubently-admins"]
            }
        }
        
        # Current authenticated user (for testing)
        self.current_user = "test@example.com"
    
    def get_jwks(self) -> dict:
        """Get the JSON Web Key Set (JWKS) for token verification."""
        # Get public key numbers
        public_numbers = self.public_key.public_numbers()
        
        # Convert to base64url-encoded values
        def int_to_base64url(n):
            """Convert integer to base64url-encoded string."""
            hex_n = format(n, 'x')
            if len(hex_n) % 2:
                hex_n = '0' + hex_n
            bytes_n = bytes.fromhex(hex_n)
            import base64
            return base64.urlsafe_b64encode(bytes_n).rstrip(b'=').decode('ascii')
        
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "mock-key-1",
                    "use": "sig",
                    "alg": "RS256",
                    "n": int_to_base64url(public_numbers.n),
                    "e": int_to_base64url(public_numbers.e)
                }
            ]
        }
    
    def create_jwt_token(
        self,
        user_email: str,
        token_type: str = "access",
        expires_in: int = 3600
    ) -> str:
        """Create a JWT token for the user."""
        user = self.users.get(user_email)
        if not user:
            raise ValueError(f"User {user_email} not found")
        
        now = datetime.now(UTC)
        exp = now + timedelta(seconds=expires_in)
        
        # Standard OIDC claims
        claims = {
            "iss": self.issuer,
            "sub": user["sub"],
            "aud": self.client_id,
            "exp": int(exp.timestamp()),
            "iat": int(now.timestamp()),
            "jti": secrets.token_urlsafe(16),
            "email": user["email"],
            "name": user["name"],
            "groups": user["groups"]
        }
        
        if token_type == "id":
            # Add additional ID token claims
            claims["nonce"] = secrets.token_urlsafe(16)
            claims["auth_time"] = int(now.timestamp())
        
        # Sign with RS256
        token = jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "mock-key-1"}
        )
        
        return token
    
    def device_authorization(self) -> dict:
        """Handle device authorization request."""
        # Generate device code and user code
        device_code = secrets.token_urlsafe(32)
        user_code = f"{secrets.randbelow(1000):03d}-{secrets.randbelow(1000):03d}"
        
        # Store pending authorization
        self.device_codes[device_code] = {
            "user_code": user_code,
            "expires_at": time.time() + 600,  # 10 minutes
            "interval": 5,
            "status": "pending",
            "user": None
        }
        
        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": f"{self.issuer}/device",
            "verification_uri_complete": f"{self.issuer}/device?user_code={user_code}",
            "expires_in": 600,
            "interval": 5
        }
    
    def device_token(self, device_code: str) -> dict:
        """Handle device token request (polling)."""
        if device_code not in self.device_codes:
            raise HTTPException(status_code=400, detail="Invalid device_code")
        
        device_info = self.device_codes[device_code]
        
        if time.time() > device_info["expires_at"]:
            del self.device_codes[device_code]
            raise HTTPException(status_code=400, detail="Device code expired")
        
        if device_info["status"] == "pending":
            raise HTTPException(status_code=428, detail="authorization_pending")
        
        if device_info["status"] == "denied":
            del self.device_codes[device_code]
            raise HTTPException(status_code=403, detail="access_denied")
        
        if device_info["status"] == "approved":
            user_email = device_info["user"]
            
            # Generate tokens
            access_token = self.create_jwt_token(user_email, "access", 3600)
            id_token = self.create_jwt_token(user_email, "id", 3600)
            refresh_token = secrets.token_urlsafe(32)
            
            # Store refresh token
            self.refresh_tokens[refresh_token] = {
                "user": user_email,
                "created_at": time.time()
            }
            
            # Clean up device code
            del self.device_codes[device_code]
            
            return {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh_token,
                "id_token": id_token
            }
        
        raise HTTPException(status_code=400, detail="Invalid device code status")
    
    def approve_device(self, user_code: str, user_email: str) -> bool:
        """Approve a device authorization request."""
        for device_code, info in self.device_codes.items():
            if info["user_code"] == user_code and info["status"] == "pending":
                info["status"] = "approved"
                info["user"] = user_email
                return True
        return False
    
    def refresh_access_token(self, refresh_token: str) -> dict:
        """Handle refresh token request."""
        if refresh_token not in self.refresh_tokens:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        
        token_info = self.refresh_tokens[refresh_token]
        user_email = token_info["user"]
        
        # Generate new access token
        access_token = self.create_jwt_token(user_email, "access", 3600)
        
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh_token
        }
    
    def get_user_info(self, access_token: str) -> dict:
        """Get user info from access token."""
        try:
            # Decode without verification (for mock purposes)
            claims = jwt.decode(
                access_token,
                self.public_key,
                algorithms=["RS256"],
                audience=self.client_id
            )
            
            return {
                "sub": claims["sub"],
                "email": claims["email"],
                "name": claims["name"],
                "groups": claims.get("groups", [])
            }
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def create_mock_oauth_app() -> FastAPI:
    """Create a FastAPI app for the mock OAuth provider."""
    app = FastAPI(title="Mock OAuth Provider")
    provider = MockOAuthProvider()
    
    @app.get("/.well-known/openid-configuration")
    async def openid_configuration():
        """OpenID Connect discovery endpoint."""
        return {
            "issuer": provider.issuer,
            "authorization_endpoint": f"{provider.issuer}/authorize",
            "token_endpoint": f"{provider.issuer}/token",
            "userinfo_endpoint": f"{provider.issuer}/userinfo",
            "jwks_uri": f"{provider.issuer}/jwks",
            "device_authorization_endpoint": f"{provider.issuer}/device/code",
            "response_types_supported": ["code", "token", "id_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "grant_types_supported": [
                "authorization_code",
                "refresh_token",
                "urn:ietf:params:oauth:grant-type:device_code"
            ]
        }
    
    @app.get("/jwks")
    async def jwks():
        """JWKS endpoint for public keys."""
        return provider.get_jwks()
    
    @app.post("/device/code")
    async def device_code():
        """Device authorization endpoint."""
        return provider.device_authorization()
    
    @app.get("/device", response_class=HTMLResponse)
    async def device_verification(user_code: str = None):
        """Device verification page."""
        html = f"""
        <html>
            <head><title>Device Authorization</title></head>
            <body>
                <h1>Device Authorization</h1>
                <form method="post" action="/device/approve">
                    <label>User Code: <input name="user_code" value="{user_code or ''}" /></label><br>
                    <label>Email: 
                        <select name="user_email">
                            <option value="test@example.com">test@example.com</option>
                            <option value="admin@example.com">admin@example.com</option>
                        </select>
                    </label><br>
                    <button type="submit">Approve</button>
                </form>
            </body>
        </html>
        """
        return html
    
    @app.post("/device/approve")
    async def device_approve(request: Request):
        """Approve device authorization."""
        form = await request.form()
        user_code = form.get("user_code")
        user_email = form.get("user_email")
        
        if provider.approve_device(user_code, user_email):
            return HTMLResponse("<h1>Device approved!</h1><p>You can close this window.</p>")
        else:
            raise HTTPException(status_code=400, detail="Invalid user code")
    
    @app.post("/token")
    async def token(request: Request):
        """Token endpoint."""
        form = await request.form()
        grant_type = form.get("grant_type")
        
        if grant_type == "urn:ietf:params:oauth:grant-type:device_code":
            device_code = form.get("device_code")
            return provider.device_token(device_code)
        
        elif grant_type == "refresh_token":
            refresh_token = form.get("refresh_token")
            return provider.refresh_access_token(refresh_token)
        
        else:
            raise HTTPException(status_code=400, detail="Unsupported grant type")
    
    @app.get("/userinfo")
    async def userinfo(request: Request):
        """User info endpoint."""
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
        
        access_token = auth_header[7:]
        return provider.get_user_info(access_token)
    
    return app


if __name__ == "__main__":
    # Run the mock provider standalone for testing
    import uvicorn
    app = create_mock_oauth_app()
    uvicorn.run(app, host="0.0.0.0", port=9000)
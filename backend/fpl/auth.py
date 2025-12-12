"""
FPL Authentication

Handle login and session management for the FPL API.
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class FPLAuth:
    """Handle FPL authentication and session management."""
    
    LOGIN_URL = "https://users.premierleague.com/accounts/login/"
    REDIRECT_URI = "https://fantasy.premierleague.com/"
    
    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        session_file: Optional[str] = None
    ):
        """
        Initialize FPL authentication.
        
        Args:
            email: FPL account email
            password: FPL account password
            session_file: Path to save/load session cookies
        """
        self.email = email or os.getenv("FPL_EMAIL")
        self.password = password or os.getenv("FPL_PASSWORD")
        self.session_file = session_file or os.getenv("FPL_SESSION_FILE", ".fpl_session.json")
        
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
        })
        
        self._authenticated = False
        self._team_id: Optional[int] = None
    
    @property
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self._authenticated
    
    @property
    def team_id(self) -> Optional[int]:
        """Get the authenticated user's team ID."""
        return self._team_id
    
    def login(self) -> bool:
        """
        Login to FPL.
        
        Returns:
            True if login successful, False otherwise
        """
        if not self.email or not self.password:
            raise ValueError("FPL_EMAIL and FPL_PASSWORD must be set")
        
        # Try to load existing session first
        if self._load_session():
            if self._verify_session():
                logger.info("Loaded existing session successfully")
                return True
            else:
                logger.info("Existing session expired, logging in fresh")
        
        # Perform fresh login
        try:
            # Get login page to get CSRF token
            login_page = self.session.get(self.LOGIN_URL)
            
            # Extract CSRF token from cookies
            csrf_token = self.session.cookies.get("csrftoken", "")
            
            # Prepare login payload
            payload = {
                "login": self.email,
                "password": self.password,
                "app": "plfpl-web",
                "redirect_uri": self.REDIRECT_URI,
            }
            
            headers = {
                "Referer": self.LOGIN_URL,
                "X-CSRFToken": csrf_token,
                "Content-Type": "application/x-www-form-urlencoded",
            }
            
            # Perform login
            response = self.session.post(
                self.LOGIN_URL,
                data=payload,
                headers=headers,
                allow_redirects=False
            )
            
            # Check for successful login (redirect or 200)
            if response.status_code in [200, 302]:
                # Verify we can access authenticated endpoints
                if self._verify_session():
                    self._save_session()
                    logger.info(f"Login successful! Team ID: {self._team_id}")
                    return True
            
            logger.error(f"Login failed with status: {response.status_code}")
            return False
            
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False
    
    def _verify_session(self) -> bool:
        """
        Verify the session is valid by accessing an authenticated endpoint.
        
        Returns:
            True if session is valid
        """
        try:
            # Try to get user's team info
            response = self.session.get(
                "https://fantasy.premierleague.com/api/me/",
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "player" in data and data["player"]:
                    self._team_id = data["player"]["entry"]
                    self._authenticated = True
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Session verification error: {e}")
            return False
    
    def _save_session(self) -> None:
        """Save session cookies to file."""
        try:
            session_data = {
                "cookies": dict(self.session.cookies),
                "team_id": self._team_id,
            }
            
            with open(self.session_file, "w") as f:
                json.dump(session_data, f)
            
            logger.debug(f"Session saved to {self.session_file}")
            
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
    
    def _load_session(self) -> bool:
        """
        Load session cookies from file.
        
        Returns:
            True if session loaded successfully
        """
        try:
            if not Path(self.session_file).exists():
                return False
            
            with open(self.session_file, "r") as f:
                session_data = json.load(f)
            
            # Restore cookies
            for name, value in session_data.get("cookies", {}).items():
                self.session.cookies.set(name, value)
            
            self._team_id = session_data.get("team_id")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            return False
    
    def logout(self) -> None:
        """Logout and clear session."""
        self.session.cookies.clear()
        self._authenticated = False
        self._team_id = None
        
        # Remove session file
        try:
            Path(self.session_file).unlink(missing_ok=True)
        except Exception:
            pass
    
    def get_session(self) -> requests.Session:
        """
        Get the authenticated session.
        
        Returns:
            requests.Session with authentication cookies
        """
        if not self._authenticated:
            self.login()
        
        return self.session


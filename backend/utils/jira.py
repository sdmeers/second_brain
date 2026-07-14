import os
import base64
import httpx
from backend.config import (
    JIRA_URL,
    JIRA_USER_EMAIL,
    JIRA_API_TOKEN,
    JIRA_DEFAULT_PROJECT_KEY
)

async def create_jira_issue(summary: str, description: str, project_key: str = None) -> str:
    """
    Creates a new task in the Jira backlog using Basic Authentication (v2 REST API).
    Returns the created issue key (e.g. 'RAID-101') if successful.
    """
    if not JIRA_USER_EMAIL or not JIRA_API_TOKEN:
        raise ValueError("JIRA_USER_EMAIL and JIRA_API_TOKEN must be configured in .env")

    # Ensure URL starts with http(s)
    jira_url = JIRA_URL
    if not jira_url.startswith("http"):
        jira_url = f"https://{jira_url}"
        
    url = f"{jira_url.rstrip('/')}/rest/api/2/issue"
    
    auth_str = f"{JIRA_USER_EMAIL}:{JIRA_API_TOKEN}"
    b64_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
    
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    pkey = project_key or JIRA_DEFAULT_PROJECT_KEY
    
    payload = {
        "fields": {
            "project": {
                "key": pkey
            },
            "summary": summary,
            "description": description,
            "issuetype": {
                "name": "Task"
            }
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers, timeout=15.0)
        
        if response.status_code == 201:
            data = response.json()
            return data["key"]
        else:
            raise Exception(f"Jira API Error {response.status_code}: {response.text}")

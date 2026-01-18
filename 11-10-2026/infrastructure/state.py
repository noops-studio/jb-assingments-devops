import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, List, Any

DB_PATH = "state.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            environment TEXT NOT NULL,
            deployment_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deployment_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            resource_name TEXT,
            metadata TEXT,
            FOREIGN KEY (deployment_id) REFERENCES deployments(deployment_id),
            UNIQUE(deployment_id, resource_type, resource_id)
        )
    """)
    
    conn.commit()
    conn.close()

def create_deployment(environment: str, deployment_id: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO deployments (environment, deployment_id, created_at, status)
            VALUES (?, ?, ?, ?)
        """, (environment, deployment_id, datetime.utcnow().isoformat(), "in_progress"))
        conn.commit()
        return deployment_id
    except sqlite3.IntegrityError:
        return deployment_id
    finally:
        conn.close()

def update_deployment_status(deployment_id: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE deployments SET status = ? WHERE deployment_id = ?
    """, (status, deployment_id))
    conn.commit()
    conn.close()

def add_resource(deployment_id: str, resource_type: str, resource_id: str, 
                resource_name: Optional[str] = None, metadata: Optional[Dict] = None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    metadata_json = json.dumps(metadata) if metadata else None
    
    cursor.execute("""
        INSERT OR REPLACE INTO resources 
        (deployment_id, resource_type, resource_id, resource_name, metadata)
        VALUES (?, ?, ?, ?, ?)
    """, (deployment_id, resource_type, resource_id, resource_name, metadata_json))
    
    conn.commit()
    conn.close()

def get_resources(deployment_id: str, resource_type: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if resource_type:
        cursor.execute("""
            SELECT resource_type, resource_id, resource_name, metadata
            FROM resources
            WHERE deployment_id = ? AND resource_type = ?
        """, (deployment_id, resource_type))
    else:
        cursor.execute("""
            SELECT resource_type, resource_id, resource_name, metadata
            FROM resources
            WHERE deployment_id = ?
        """, (deployment_id,))
    
    results = []
    for row in cursor.fetchall():
        metadata = json.loads(row[3]) if row[3] else {}
        results.append({
            "resource_type": row[0],
            "resource_id": row[1],
            "resource_name": row[2],
            "metadata": metadata
        })
    
    conn.close()
    return results

def get_deployment_id(environment: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT deployment_id FROM deployments
        WHERE environment = ? AND status = 'completed'
        ORDER BY created_at DESC
        LIMIT 1
    """, (environment,))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None

def delete_deployment(deployment_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM resources WHERE deployment_id = ?", (deployment_id,))
    cursor.execute("DELETE FROM deployments WHERE deployment_id = ?", (deployment_id,))
    
    conn.commit()
    conn.close()

def get_resource_by_type(deployment_id: str, resource_type: str) -> Optional[Dict[str, Any]]:
    resources = get_resources(deployment_id, resource_type)
    return resources[0] if resources else None

def get_all_deployments() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT environment, deployment_id, created_at, status
        FROM deployments
        WHERE status IN ('completed', 'in_progress')
        ORDER BY created_at DESC
    """)
    
    results = []
    for row in cursor.fetchall():
        results.append({
            "environment": row[0],
            "deployment_id": row[1],
            "created_at": row[2],
            "status": row[3]
        })
    
    conn.close()
    return results

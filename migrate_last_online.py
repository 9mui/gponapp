#!/usr/bin/env python3
"""
Migration script to populate last_online timestamps for existing ONUs
This should be run once after updating the schema
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from models import db
from datetime import datetime, timezone, timedelta

def migrate_last_online_timestamps():
    """
    Populate last_online timestamps for existing ONUs based on their first_seen time
    This provides a reasonable fallback for ONUs that were discovered before we started tracking last_online
    """
    print("Migrating last_online timestamps for existing ONUs...")
    
    try:
        with db() as conn:
            # Get all ONUs that don't have last_online set
            rows = conn.execute("""
                SELECT sn_norm, first_seen 
                FROM onu_seen 
                WHERE last_online IS NULL AND first_seen IS NOT NULL
            """).fetchall()
            
            if not rows:
                print("No ONUs need migration - all already have last_online timestamps")
                return
            
            print(f"Found {len(rows)} ONUs that need last_online timestamps")
            
            # Set last_online to first_seen for these ONUs as a reasonable default
            # This assumes they were online when first discovered
            migrated = 0
            for sn_norm, first_seen in rows:
                conn.execute("""
                    UPDATE onu_seen 
                    SET last_online = first_seen 
                    WHERE sn_norm = ? AND last_online IS NULL
                """, (sn_norm,))
                migrated += 1
            
            print(f"✓ Successfully migrated {migrated} ONU records")
            print("✓ Migration completed successfully")
            
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        return False
    
    return True

if __name__ == "__main__":
    migrate_last_online_timestamps()
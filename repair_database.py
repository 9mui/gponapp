#!/usr/bin/env python3
"""
Database repair script for GPON App
Fixes duplicate entries and constraint issues
"""

import sqlite3
import sys
from pathlib import Path

def repair_database():
    """Repair database by removing duplicates and fixing constraints"""
    db_path = Path(__file__).parent / "instance" / "onulist.db"
    
    if not db_path.exists():
        print("Database file not found. Nothing to repair.")
        return True
    
    print("Starting database repair...")
    
    try:
        # Create backup first
        backup_path = db_path.with_suffix('.db.backup')
        print(f"Creating backup: {backup_path}")
        
        with open(db_path, 'rb') as src, open(backup_path, 'wb') as dst:
            dst.write(src.read())
        
        # Connect and repair
        conn = sqlite3.connect(db_path)
        
        print("Checking for duplicate entries in onu_seen...")
        
        # Check for duplicates in onu_seen table
        duplicates = conn.execute("""
            SELECT sn_norm, COUNT(*) as count 
            FROM onu_seen 
            GROUP BY sn_norm 
            HAVING COUNT(*) > 1
        """).fetchall()
        
        if duplicates:
            print(f"Found {len(duplicates)} duplicate SN entries")
            
            # Remove duplicates, keeping the earliest entry
            for sn_norm, count in duplicates:
                print(f"Fixing duplicate SN: {sn_norm} ({count} entries)")
                
                # Keep only the earliest entry
                conn.execute("""
                    DELETE FROM onu_seen 
                    WHERE sn_norm = ? AND rowid NOT IN (
                        SELECT MIN(rowid) 
                        FROM onu_seen 
                        WHERE sn_norm = ?
                    )
                """, (sn_norm, sn_norm))
        else:
            print("No duplicates found in onu_seen table")
        
        # Check for orphaned gpon entries
        print("Checking for orphaned GPON entries...")
        orphaned = conn.execute("""
            SELECT COUNT(*) FROM gpon g
            WHERE NOT EXISTS (
                SELECT 1 FROM olts o WHERE o.ip = g.olt_ip
            )
        """).fetchone()[0]
        
        if orphaned > 0:
            print(f"Found {orphaned} orphaned GPON entries, cleaning up...")
            conn.execute("""
                DELETE FROM gpon 
                WHERE olt_ip NOT IN (SELECT ip FROM olts)
            """)
        
        # Vacuum database to reclaim space and rebuild indexes
        print("Vacuuming database...")
        conn.execute("VACUUM")
        
        # Commit changes
        conn.commit()
        conn.close()
        
        print("✓ Database repair completed successfully")
        print(f"✓ Backup saved to: {backup_path}")
        return True
        
    except Exception as e:
        print(f"✗ Database repair failed: {e}")
        return False

def main():
    print("GPON App Database Repair Tool")
    print("=" * 40)
    
    if repair_database():
        print("\nDatabase repair completed successfully!")
        print("You can now restart the application.")
        return 0
    else:
        print("\nDatabase repair failed!")
        print("Check the error messages above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
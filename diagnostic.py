#!/usr/bin/env python3
"""
GPON App Diagnostic Tool
Test database connections, SNMP functionality, and common issues
"""

import sys
import os
import sqlite3
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

def test_database():
    """Test database connectivity and schema"""
    print("=== Testing Database ===")
    try:
        from models import db, ensure_db
        
        # Test database initialization
        ensure_db()
        print("✓ Database initialization successful")
        
        # Test database connection
        with db() as conn:
            result = conn.execute("SELECT COUNT(*) FROM olts").fetchone()
            print(f"✓ Database connection successful, found {result[0]} OLTs")
            
            # Test schema
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            expected_tables = ['olts', 'ponports', 'gpon', 'onu_notes', 'onu_seen']
            found_tables = [t[0] for t in tables]
            
            for table in expected_tables:
                if table in found_tables:
                    print(f"✓ Table '{table}' exists")
                else:
                    print(f"✗ Table '{table}' missing")
                    
    except Exception as e:
        print(f"✗ Database test failed: {e}")
        return False
    
    return True

def test_snmp():
    """Test SNMP functionality"""
    print("\n=== Testing SNMP ===")
    try:
        from snmp import snmpwalk
        
        # Test SNMP command availability
        import subprocess
        result = subprocess.run(['which', 'snmpwalk'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ snmpwalk command found")
        else:
            print("✗ snmpwalk command not found - install net-snmp package")
            return False
            
        # Test SNMP cache
        from snmp import _snmp_cache
        print(f"✓ SNMP cache initialized, size: {len(_snmp_cache)}")
        
    except Exception as e:
        print(f"✗ SNMP test failed: {e}")
        return False
    
    return True

def test_application():
    """Test basic application functionality"""
    print("\n=== Testing Application ===")
    try:
        from app import app
        
        # Test Flask app creation
        print("✓ Flask application created successfully")
        
        # Test route registration
        routes = [rule.rule for rule in app.url_map.iter_rules()]
        expected_routes = ['/', '/olt/<ip>/refresh', '/search']
        
        for route in expected_routes:
            if any(route in r for r in routes):
                print(f"✓ Route '{route}' registered")
            else:
                print(f"✗ Route '{route}' missing")
                
    except Exception as e:
        print(f"✗ Application test failed: {e}")
        return False
    
    return True

def test_configuration():
    """Test configuration and environment"""
    print("\n=== Testing Configuration ===")
    try:
        # Test configuration file
        if os.path.exists('config.py'):
            print("✓ Configuration file found")
        else:
            print("⚠ Configuration file not found (optional)")
        
        # Test log file permissions
        try:
            with open('gponapp.log', 'a') as f:
                f.write("# Diagnostic test\n")
            print("✓ Log file writable")
        except Exception as e:
            print(f"⚠ Log file issue: {e}")
        
        # Test database directory
        db_dir = Path("instance")
        if db_dir.exists():
            print("✓ Database directory exists")
        else:
            print("✓ Database directory will be created")
            
    except Exception as e:
        print(f"✗ Configuration test failed: {e}")
        return False
    
    return True

def main():
    print("GPON App Diagnostic Tool")
    print("=" * 50)
    
    tests = [
        test_configuration,
        test_database,
        test_snmp,
        test_application,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ Test {test.__name__} crashed: {e}")
            failed += 1
    
    print(f"\n=== Summary ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    if failed == 0:
        print("✓ All tests passed! The application should work correctly.")
        return 0
    else:
        print("✗ Some tests failed. Check the errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
#!/bin/bash
# Quick fix script for missing APScheduler dependency

echo "GPON App Quick Fix - Installing Missing Dependencies"
echo "=================================================="

# Check if we're in a virtual environment
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "✓ Virtual environment detected: $VIRTUAL_ENV"
else
    echo "⚠ No virtual environment detected. Creating one..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "✓ Virtual environment created and activated"
fi

# Install/update requirements
echo "Installing requirements..."
pip install --upgrade pip
pip install -r requirements.txt

if [ $? -eq 0 ]; then
    echo "✓ Dependencies installed successfully"
else
    echo "✗ Failed to install dependencies"
    exit 1
fi

# Run diagnostic again
echo ""
echo "Running diagnostic test..."
python3 diagnostic.py

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ All tests passed! You can now start the application:"
    echo "python3 app.py"
else
    echo ""
    echo "Some issues remain. Check the diagnostic output above."
fi
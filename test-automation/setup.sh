#!/bin/bash

# Setup script for test automation dependencies

echo "Setting up Kubently test automation environment..."

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "✓ Setup complete!"
echo ""
echo "To use the test automation, activate the virtual environment:"
echo "  source venv/bin/activate"
echo ""
echo "Then run tests:"
echo "  python3 comprehensive_test_runner.py --api-key YOUR_KEY --analyze-previous 01-imagepullbackoff-typo_20250908_084451.json"
echo ""
echo "Or use the wrapper script which handles activation:"
echo "  ./run_tests.sh comprehensive 01-imagepullbackoff-typo"
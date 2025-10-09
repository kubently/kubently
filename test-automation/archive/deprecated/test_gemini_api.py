#!/usr/bin/env python3
"""
Quick test to verify Gemini API key works
"""

import os
import sys

try:
    import google.generativeai as genai
except ImportError:
    print("Error: google-generativeai not installed")
    print("Run: pip install google-generativeai")
    sys.exit(1)

api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("Error: GOOGLE_API_KEY environment variable not set")
    print("Set it with: export GOOGLE_API_KEY='your-key-here'")
    sys.exit(1)

print(f"✓ API Key found (length: {len(api_key)})")

try:
    # Configure Gemini
    genai.configure(api_key=api_key)
    
    # List available models to verify connection
    print("\nAvailable Gemini models:")
    for model in genai.list_models():
        if 'generateContent' in model.supported_generation_methods:
            print(f"  • {model.name}")
    
    # Test with a simple prompt
    print("\nTesting Gemini 2.0 Flash...")
    model = genai.GenerativeModel('gemini-2.0-flash')
    response = model.generate_content("Say 'API key is working!' in 5 words or less")
    
    print(f"Response: {response.text}")
    print("\n✓ Gemini API is working correctly!")
    
except Exception as e:
    print(f"\n✗ Error: {e}")
    print("\nPossible issues:")
    print("1. Invalid API key")
    print("2. API key doesn't have Gemini access enabled")
    print("3. Network connection issues")
    sys.exit(1)
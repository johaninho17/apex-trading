"""
conftest.py — pytest configuration for the Apex backend test suite.
Ensures the backend root is on sys.path so all `services.*` imports resolve.
"""
import os
import sys

# Add the backend directory to sys.path so imports work without installation
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

#!/bin/bash
echo "🎨 Starting Apex Frontend (Vite + React)..."
cd frontend

if [ ! -d "node_modules" ]; then
    echo "   Installing Frontend dependencies..."
    npm install
fi

# Bypass WSL NTFS symlink bug by executing the script via Node directly
node node_modules/vite/bin/vite.js --host

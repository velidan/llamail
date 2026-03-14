#!/bin/bash

echo "🚀 Setting up n8n Email Intelligence System..."

# Create .env from example if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✅ Created .env file from template"
    echo "   ⚠️  Please edit .env to change default password!"
else
    echo "ℹ️  .env already exists, skipping..."
fi

# Create necessary directories
mkdir -p data logs

echo ""
echo "📦 Starting n8n..."
docker compose up -d

echo ""
echo "⏳ Waiting for n8n to start..."
sleep 10

# Check if n8n is running
if docker compose ps | grep -q "running"; then
    echo ""
    echo "✅ n8n is running!"
    echo ""
    echo "🌐 Access n8n at: http://localhost:5678"
    echo "   Username: admin"
    echo "   Password: changeme123 (change in .env)"
    echo ""
    echo "📝 Next steps:"
    echo "   1. Open http://localhost:5678 in your browser"
    echo "   2. Log in with the credentials above"
    echo "   3. Create a new workflow to test"
    echo ""
else
    echo "❌ n8n failed to start. Check logs with: docker compose logs n8n"
fi

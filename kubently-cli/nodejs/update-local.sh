#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🔄 Updating Kubently CLI...${NC}"

# Step 1: Clean old build
echo -e "\n${YELLOW}1/4${NC} Cleaning old build..."
npm run clean

# Step 2: Build TypeScript
echo -e "\n${YELLOW}2/4${NC} Building TypeScript..."
npm run build

# Step 3: Update global link
echo -e "\n${YELLOW}3/4${NC} Updating global npm link..."
npm unlink -g kubently-cli 2>/dev/null || true
npm link

# Step 4: Refresh asdf shims (if asdf is installed)
echo -e "\n${YELLOW}4/4${NC} Refreshing asdf shims..."
if command -v asdf &> /dev/null; then
    asdf reshim nodejs
    echo -e "${GREEN}✓${NC} asdf shims refreshed"
else
    echo -e "${YELLOW}⚠${NC}  asdf not found, skipping reshim"
fi

# Verify installation
echo -e "\n${GREEN}✅ Update complete!${NC}"
echo -e "\n${GREEN}Installed version:${NC}"
kubently --version || echo -e "${RED}✗${NC} Could not verify installation"

# Show where kubently is installed
echo -e "\n${GREEN}Command location:${NC}"
which kubently || echo -e "${RED}✗${NC} kubently not found in PATH"

echo -e "\n${YELLOW}💡 Tip:${NC} If changes don't appear, run: ${GREEN}hash -r${NC}"

#!/usr/bin/env bash
# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License").

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

INSTALL_ARGS=()
UI_ARGS=()
for arg in "$@"; do
    if [ "${arg}" = "--with-ios" ]; then
        INSTALL_ARGS+=("${arg}")
    else
        UI_ARGS+=("${arg}")
    fi
done

echo -e "${BOLD}${CYAN}======================================================${NC}"
echo -e "${BOLD}${CYAN}      Artemis Autonomous Mobile Agent UI             ${NC}"
echo -e "${BOLD}${CYAN}======================================================${NC}"
echo ""

# Keep first-run setup in one implementation so start.sh and the standalone
# installer install exactly the same supported dependency set.
bash "${SCRIPT_DIR}/scripts/install_deps.sh" "${INSTALL_ARGS[@]}"

echo ""
echo -e "   ${CYAN}Would you like to configure ARTEMIS MCP and testing rules for your AI IDEs?${NC}"
if [ -t 0 ]; then
    read -r -p "      Install MCP configuration and rules now? [Y/n]: " INSTALL_MCP
    INSTALL_MCP=${INSTALL_MCP:-Y}
else
    INSTALL_MCP="n"
fi

if [[ "${INSTALL_MCP}" =~ ^[Yy]$ ]]; then
    if uv run artemis mcp --install all; then
        echo -e "   ${GREEN}MCP configuration and rules installed.${NC}"
    else
        echo -e "   ${YELLOW}MCP setup failed; retry with: uv run artemis mcp --install all${NC}"
    fi
fi

OPEN_FLAG="--open"
if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_CLIENT:-}" ] || [ -n "${SSH_TTY:-}" ] || [ -z "${DISPLAY:-}" ]; then
    OPEN_FLAG="--no-open"
fi
for arg in "${UI_ARGS[@]}"; do
    case "${arg}" in
        --open|--no-open) OPEN_FLAG="" ;;
    esac
done

echo -e "   ${GREEN}Launching Artemis Showcase UI and Admin Console...${NC}"
if [ -n "${OPEN_FLAG}" ]; then
    exec uv run python -m artemis ui "${OPEN_FLAG}" "${UI_ARGS[@]}"
else
    exec uv run python -m artemis ui "${UI_ARGS[@]}"
fi

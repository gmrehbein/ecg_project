#!/bin/bash
set -euo pipefail


# --- COLORS ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

# --- HELP MESSAGE ---
show_help() {
  echo -e "${CYAN}Usage:${RESET} $0 [OPTIONS]"
  echo
  echo -e "${YELLOW}Options:${RESET}"
  echo -e "  ${GREEN}--build${RESET}         Build all Docker images before starting containers."
  echo -e "  ${GREEN}--build-only${RESET}    Build images and exit without starting containers."
  echo -e "  ${GREEN}--help${RESET}          Show this help message and exit."
  echo
}

# --- REUSABLE FUNCTION TO WAIT FOR DEVICE AND SOCKET READY CONDITIONS ---
wait_for_condition() {
  local description="$1"
  local condition="$2"
  local timeout="$3"

  echo -e "${CYAN}Waiting for ${description}...${RESET}"
  local start_time
  start_time=$(date +%s)

  for ((i=1; i<=timeout; i++)); do
    if eval "$condition" &>/dev/null; then
      echo -e "${GREEN}✅ ${description} is ready.${RESET}"
      return 0
    fi

    local now
    now=$(date +%s)
    local elapsed=$((now - start_time))
    local percent=$((100 * elapsed / timeout))

    echo -e "${YELLOW}⌛ Waiting for ${description}... (${elapsed}s/${timeout}s, ${percent}%)${RESET}"
    sleep 1
  done

  echo -e "${RED}❌ ${description} not ready after ${timeout} seconds.${RESET}"

  # --- Offer user to continue or abort ---
  read -rp "$(echo -e "${YELLOW}❓ Timeout reached. Continue anyway? (y/n): ${RESET}")" choice
  case "$choice" in
    y|Y)
      echo -e "${YELLOW}⚠️  Continuing despite timeout...${RESET}"
      return 0
      ;;
    *)
      echo -e "${RED}🛑 Aborting script.${RESET}"
      exit 1
      ;;
  esac
}


# --- FUNCTION TO FIND FREE APP PORT ---
# This function finds a free port for the app container to use if the default port (5000) is already in use.
find_free_port() {
  local base_port=$1
  local max_port=$((base_port + 20))
  for ((port=base_port; port<=max_port; port++)); do
    if ! lsof -i tcp:${port} &>/dev/null; then
      echo $port
      return
    fi
  done
  echo -e "${RED}❌ No free ports available in range ${base_port}-${max_port}.${RESET}"
  exit 1
}


# --- DEFAULT FLAGS ---
BUILD_IMAGES=false
BUILD_ONLY=false

# --- LOAD .env ---
set -o allexport
[ -f .env ] && source .env
set +o allexport

# --- PARSE CLI OPTIONS ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      BUILD_IMAGES=true
      shift
      ;;
    --build-only)
      BUILD_IMAGES=true
      BUILD_ONLY=true
      shift
      ;;
    --help)
      show_help
      exit 0
      ;;
    *)
      echo -e "${RED}❌ Unknown option: $1${RESET}"
      show_help
      exit 1
      ;;
  esac
done

# --- SETUP VARIABLES ---
SCRIPTPATH="$( cd "$(dirname "$0")" ; pwd -P )"
cd "$SCRIPTPATH"

SIGNAL_PROCESSOR_HOST=176.33.0.2
SIGNAL_PROCESSOR_PORT=9999
SIGNAL_PROCESSOR_ADDRESS="tcp://${SIGNAL_PROCESSOR_HOST}:${SIGNAL_PROCESSOR_PORT}"
DEVICE_WAIT_TIMEOUT=30
SOCKET_WAIT_TIMEOUT=30


# --- FIND FREE PORT FOR APP GUI ---
APP_PORT=$(find_free_port 5000)
echo -e "${CYAN}Using port ${APP_PORT} for the ECG GUI.${RESET}"


mkdir -p ./ecg_database
rm -f ./dev/device

# --- CREATE NETWORK ---
if ! docker network ls | grep -q "test-project-network"; then
  echo -e "${YELLOW}Creating Docker network: test-project-network${RESET}"
  docker network create \
    --subnet=176.33.0.0/24 \
    --gateway=176.33.0.1 \
    test-project-network
fi

# --- BUILD IMAGES ---
if $BUILD_IMAGES; then
  echo -e "${CYAN}Building Docker images...${RESET}"
  docker build -t test-project/ecg_simulator:1.0.1 ./ecg_simulator
  docker build -t test-project/signal_processing:1.0.1 ./signal_processing
  docker build -t test-project/app:1.0.1 ./app
fi

if $BUILD_ONLY; then
  echo -e "${GREEN}✅ Build complete. Exiting (--build-only).${RESET}"
  exit 0
fi

# --- START SIMULATOR ---
echo -e "${CYAN}Starting ecg_simulator...${RESET}"
docker run --rm -d \
  --privileged \
  --name ecg_simulator \
  --network none \
  --security-opt no-new-privileges \
  --security-opt seccomp=unconfined \
  -v "${SCRIPTPATH}/ecg_config:/root/ecg_config" \
  -v /dev/pts:/dev/pts \
  -v "${SCRIPTPATH}/dev:/root/dev" \
  test-project/ecg_simulator:1.0.1

# --- WAIT FOR SERIAL DEVICE SYMLINK TO BE CREATED ---
wait_for_condition \
"virtual device /root/dev/device inside ecg_simulator container" \
"docker exec ecg_simulator test -e /root/dev/device" \
"$DEVICE_WAIT_TIMEOUT"


# --- START SIGNAL PROCESSOR ---
echo -e "${CYAN}Starting signal_processing...${RESET}"
docker run --rm -d \
  --name signal_processing \
  --network test-project-network \
  --ip ${SIGNAL_PROCESSOR_HOST} \
  -v "${SCRIPTPATH}/ecg_config:/root/ecg_config" \
  -v "${SCRIPTPATH}/dev:/root/dev" \
  -v /dev/pts:/dev/pts \
  test-project/signal_processing:1.0.1

# --- WAIT FOR PUB SOCKET TO BE READY ---
wait_for_condition \
  "pub socket at localhost:${SIGNAL_PROCESSOR_PORT} inside signal_processing container" \
  "docker exec signal_processing bash -c \"timeout 1 bash -c '</dev/tcp/localhost/${SIGNAL_PROCESSOR_PORT}'\"" \
  "$SOCKET_WAIT_TIMEOUT"

# --- START APP ---
echo -e "${CYAN}Starting app container...${RESET}"
docker run --rm -d \
  --name app \
  --network test-project-network \
  --ip 176.33.0.3 \
  -v "${SCRIPTPATH}/ecg_config:/root/ecg_config" \
  -v "${SCRIPTPATH}/ecg_database:/root/ecg_database" \
  -p ${APP_PORT}:5000 \
  -e SIGNAL_PROCESSOR_ADDRESS=${SIGNAL_PROCESSOR_ADDRESS} \
  test-project/app:1.0.1

# --- DONE ---
echo -e "${GREEN}🎉 All containers launched.${RESET}"
echo -e "${CYAN}🌐 ECG GUI available at: http://localhost:${APP_PORT}${RESET}"
echo -e "${YELLOW}🛑 To stop all containers, run: ./stop_containers.sh${RESET}"
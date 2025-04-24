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

# --- WAIT FOR DEVICE SYMLINK ---
echo -e "${CYAN}Waiting for /root/dev/device to appear...${RESET}"
for i in {1..10}; do
  if [ -e "${SCRIPTPATH}/dev/device" ]; then
    echo -e "${GREEN}✅ Found device: ./dev/device${RESET}"
    break
  fi
  echo -e "${YELLOW}⌛ Waiting... ($i)${RESET}"
  sleep 1
done

if [ ! -e "${SCRIPTPATH}/dev/device" ]; then
  echo -e "${RED}⚠️  Warning: dev/device was not found after 10 seconds.${RESET}"
fi

# --- START SIGNAL PROCESSOR ---
echo -e "${CYAN}Starting signal_processing...${RESET}"
docker run -d \
  --name signal_processing \
  --network test-project-network \
  --ip ${SIGNAL_PROCESSOR_HOST} \
  -v "${SCRIPTPATH}/ecg_config:/root/ecg_config" \
  -v "${SCRIPTPATH}/dev:/root/dev" \
  -v /dev/pts:/dev/pts \
  test-project/signal_processing:1.0.1

# --- WAIT FOR PUB SOCKET TO BE READY ---
echo -e "${CYAN}Waiting for signal processor to open pub socket...${RESET}"
for i in {1..10}; do
  if timeout 1 bash -c "</dev/tcp/${SIGNAL_PROCESSOR_HOST}/${SIGNAL_PROCESSOR_PORT}" 2>/dev/null; then
    echo -e "${GREEN}✅ Pub socket is ready at ${SIGNAL_PROCESSOR_ADDRESS}${RESET}"
    break
  fi
  echo -e "${YELLOW}⌛ Socket not ready... ($i)${RESET}"
  sleep 1
done

# --- START APP ---
echo -e "${CYAN}Starting app container...${RESET}"
docker run -d \
  --name app \
  --network test-project-network \
  --ip 176.33.0.3 \
  -v "${SCRIPTPATH}/ecg_config:/root/ecg_config" \
  -v "${SCRIPTPATH}/ecg_database:/root/ecg_database" \
  -p 5000:5000 \
  -e SIGNAL_PROCESSOR_ADDRESS=${SIGNAL_PROCESSOR_ADDRESS} \
  test-project/app:1.0.1

# --- DONE ---
echo -e "${GREEN}🎉 All containers launched.${RESET}"
echo -e "${CYAN}🌐 ECG GUI available at: http://localhost:5000${RESET}"
echo -e "${YELLOW}🛑 To stop all containers, run: ./stop_containers.sh${RESET}"

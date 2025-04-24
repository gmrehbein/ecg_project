# ECG Visualization Project

## Introduction

In this project, you will build a system for acquiring, processing, and visualizing electrocardiogram (ECG) signals. You'll work with Docker containers, serial communications, network protocols, data processing, and visualization.

## System Architecture

The system consists of three main components:

1. **Hardware Simulator**: A pre-built Docker container that simulates an ECG device, generating raw electrode signals and making them available through a virtual serial port.

2. **Signal Processing Unit**: A component you will build that:
   - Reads raw electrode data from the hardware simulator via serial connection
   - Processes the data to calculate standard ECG leads
   - Publishes the processed ECG data over a network connection

3. **Visualization Application**: A component you will build that:
   - Receives processed ECG data from the signal processing unit
   - Stores the data in a database for historical review
   - Displays the ECG signals in real-time on a web interface

## Challenge Requirements

### Required Tasks

1. **Signal Processing Unit**:
   - Create a Docker container for the signal processing unit
   - Implement serial communication to read electrode data from the hardware simulator
   - Calculate the six standard ECG leads
   - Develop a network interface to publish the processed signals

2. **Visualization Application**:
   - Create a Docker container for the visualization application
   - Implement a database to store ECG signals
   - Update the existing visualization application to display the ECG signals in real-time (or develop your own)
   - Ensure the system can handle disconnections and reconnections

3. **Testing**:
   - Develop appropriate tests for your components
   - Document your testing approach and results

### Bonus Tasks

1. Calculate the heart rate from the ECG signals and display it on the web interface
2. Implement filtering to remove power line interference and remove the DC offset
3. Enhance or build your own visualization application.

## Technical Background: ECG Leads

A standard 6-lead ECG uses three electrodes placed on the body:
- Right Arm (RA)
- Left Arm (LA)
- Left Leg (LL)

From these three electrodes, six standard leads are calculated:

1. **Bipolar Limb Leads**:
   - Lead I = LA - RA
   - Lead II = LL - RA
   - Lead III = LL - LA

2. **Augmented Unipolar Limb Leads**:
   - aVR = RA - (LA + LL)/2
   - aVL = LA - (RA + LL)/2
   - aVF = LL - (RA + LA)/2

These six leads provide different "views" of the heart's electrical activity and are essential for clinical interpretation.

## Technical Details

### Hardware Simulator Interface

The hardware simulator generates synthetic ECG data and makes it available through a virtual serial port created using `socat`. The simulator creates two virtual serial ports in the `test_project_files/dev` folder:

- **Client Port**: `test_project_files/dev/device` (should be mapped into your signal processing container)
- **Simulator Port**: `test_project_files/dev/sim-device` (used internally by simulator)

To access the ECG data in your signal processing container, you must mount the `test_project_files/dev` folder and `/dev/pts` as volumes when running your container. For example:

```bash
docker run -d \
  -v $(pwd)/test_project_files/dev:/root/dev \
  -v /dev/pts:/dev/pts \
  ...
```

The simulator will send JSON objects with the raw electrode values to the client port at a rate of 100 Hz.
- **Baud Rate**: 115200
- **Data Format**: JSON objects sent line by line
- **Sample Rate**: 100 Hz

Each JSON object contains the raw electrode values:

```json
{"RA": 0.45, "LA": 0.52, "LL": 0.48}
```

The values represent voltages in a 0-1V range.

### Container Networking

The system uses a dedicated Docker network called `test-project-network` (subnet 176.33.0.0/24) to enable communication between containers:

- **Signal Processing**: Connected at IP 176.33.0.2
- **Visualization App**: Connected at IP 176.33.0.3, exposes port 5000 for the web interface

The network configuration ensures:
- Isolated communication between containers
- Fixed IP addresses for reliable service discovery
- Port mapping to host machine for web access
- Network security through container isolation

The network is automatically created by the run script.


### Signal Publishing Considerations

When designing your signal publishing solution, consider these real-world factors:

- **Latency**: Minimize delay between acquisition and display
- **Reliability**: Handle network disruptions gracefully
- **Scalability**: Design for potential additional clients
- **Resource Usage**: Optimize CPU and memory consumption
- **Synchronization**: Ensure all signals are properly time-aligned

## Getting Started

1. Copy this repository to a linux machine
2. Run the hardware simulator:
   ```bash
   ./run-containers.sh
   ```
3. Build and run your signal processing container
4. Build and run your visualization application container (see below)
5. Open the web interface to view the ECG signals

## Visualisation Container

This project includes a visualization container in the `app` directory that you can use as a starting point. You're welcome to use it as-is, modify it to meet your needs, or create your own visualization solution from scratch.

To build and run the provided visualization container:

1. Build the container:
   ```bash
   docker build -t test-project/app:1.0.1 ./app
   ```

2. Run the container:
   ```bash
   docker run --rm -d \
     --name app \
     --network test-project-network \
     --ip 176.33.0.3 \
     -p 5000:5000 \
     test-project/app:1.0.1
   ```

The visualization will be available at http://localhost:5000

## Directory Structure

```
test_project_files/          # Sample files and templates
  ├── app/                   # Sample visualization app template
  ├── signal_processing/     # Signal processing placeholder
  ├── dev/                   # Hardware simulator virtual serial ports shared between containers
  ├── hardware-simulator.tar # Pre-built ECG signal simulator (DO NOT MODIFY)
  ├── README.md              # This file
  └── run-containers.sh      # Script to run the hardware simulator
```

## Development system

This project is intended to be run on a Linux system with Docker installed. If you need to install Docker, please follow the official installation instructions at: https://docs.docker.com/engine/install/

The project has been tested on:
- Ubuntu 20.04 LTS
- Ubuntu 22.04 LTS
- Ubuntu 24.04 LTS 
- Windows Subsystem for Linux (WSL2) running Ubuntu

Make sure you have Docker installed and running before proceeding with the setup steps above.


## Evaluation Criteria

Your solution will be evaluated based on:

1. **Functionality**: Does the system acquire, process, and display ECG signals correctly?
2. **Code Quality**: Is the code well-structured, documented, and maintainable?
3. **System Design**: How well do the components interact with each other?
4. **Error Handling**: How robustly does the system handle failures and edge cases?
5. **Performance**: How efficiently does the system process and display data?
6. **Testing**: How thoroughly is the system tested?

## AI Tools Usage

The use of AI tools such as GitHub Copilot, Cursor, and ChatGPT is permitted for this project. These
tools can help with code generation, problem-solving, and documentation.

However, please note that during the onsite interview, the team will conduct a deep-dive into your code
implementation and will ask very detailed questions about your solution. You should be prepared to
explain every aspect of your code, including design decisions, implementation details, and technical
trade-offs.


## Submission

Please submit your solution as tar/zip file or git repository:

1. Source code for all components
2. Docker configuration files
3. Documentation explaining your approach and design decisions
4. Instructions for running and testing your solution

Good luck!

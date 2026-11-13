# Local CPU ROS2 Humble participant, not a validated Jetson hardware image.
FROM ros:humble-ros-base-jammy
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-colcon-common-extensions python3-pil python3-opencv ros-humble-geometry-msgs ros-humble-nav-msgs ros-humble-sensor-msgs ros-humble-launch-ros libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir 'torch==2.6.0' 'torchvision==0.21.0' --index-url https://download.pytorch.org/whl/cpu
RUN python3 -m pip install --no-cache-dir 'numpy<2' 'ultralytics==8.4.39'
WORKDIR /opt/maicon
COPY ros2/src /opt/maicon/src
RUN source /opt/ros/humble/setup.bash && colcon build
COPY ros2/participant-container.py /participant-container.py
COPY ros2/participant-entrypoint.sh /participant-entrypoint.sh
RUN chmod +x /participant-entrypoint.sh
ENTRYPOINT ["/participant-entrypoint.sh"]

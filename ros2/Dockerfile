# ROS Humble targets Ubuntu 22.04. This image is not a Jetson GPU runtime.
FROM ros:humble-ros-base-jammy
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-colcon-common-extensions python3-pil \
    ros-humble-geometry-msgs ros-humble-nav-msgs ros-humble-sensor-msgs \
    ros-humble-launch-ros && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY ros2/src /workspace/src
COPY examples /workspace/examples
RUN source /opt/ros/humble/setup.bash && colcon build --symlink-install
ENV PYTHONPATH=/workspace/examples
COPY ros2/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "maicon_sim_bridge", "simulator.launch.py"]

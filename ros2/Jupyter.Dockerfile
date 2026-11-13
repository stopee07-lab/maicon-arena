# Actual Ubuntu 22.04 + ROS 2 Humble notebook kernel; not a Jetson runtime.
FROM maicon-rebuild-participant:humble
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-opencv python3-matplotlib ros-humble-cv-bridge \
    && rm -rf /var/lib/apt/lists/*
# Humble cv_bridge and system OpenCV are compiled against the NumPy 1.x ABI.
RUN python3 -m pip install --no-cache-dir 'numpy<2' 'jupyterlab>=4.4,<5' 'ipykernel>=6,<7' 'ipywidgets>=8,<9'
RUN mkdir -p /usr/local/share/jupyter/kernels/maicon-ros2 && \
    printf '%s\n' '{"argv":["/usr/bin/python3","-m","ipykernel_launcher","-f","{connection_file}"],"display_name":"Python 3 (ROS 2 · simulation)","language":"python"}' \
    > /usr/local/share/jupyter/kernels/maicon-ros2/kernel.json
COPY ros2/jupyter-entrypoint.sh /jupyter-entrypoint.sh
RUN chmod 755 /jupyter-entrypoint.sh
ENV PYTHONPATH=/team
ENV HOME=/tmp/maicon-home JUPYTER_CONFIG_DIR=/tmp/maicon-home/.jupyter \
    JUPYTER_RUNTIME_DIR=/tmp/maicon-home/jupyter-runtime IPYTHONDIR=/tmp/maicon-home/.ipython
WORKDIR /team
ENTRYPOINT ["/jupyter-entrypoint.sh"]

import os
from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([Node(package='maicon_sim_bridge',executable='bridge',
        name='maicon_sim_bridge',output='screen',parameters=[{
            'server_url':os.environ.get('SIM_SERVER','http://host.docker.internal:8765'),
            'mission_code':os.environ.get('MISSION_CODE',''),
            'sim_mode':os.environ.get('SIM_MODE','match'),
            'read_only':os.environ.get('SIM_READ_ONLY','false').lower()=='true',
            'robot_namespace':os.environ.get('ROBOT_NAMESPACE','')
        }])])

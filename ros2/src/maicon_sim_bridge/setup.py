from setuptools import setup
from glob import glob
setup(name='maicon_sim_bridge',version='0.1.0',packages=['maicon_sim_bridge'],
      data_files=[('share/ament_index/resource_index/packages',['resource/maicon_sim_bridge']),
                  ('share/maicon_sim_bridge',['package.xml']),
                  ('share/maicon_sim_bridge/launch',glob('launch/*.launch.py'))],
      install_requires=['setuptools'],zip_safe=True,
      maintainer='MAICON local team',maintainer_email='local@example.invalid',
      description='Simulation-only HTTP sensor bridge',license='Proprietary',
      entry_points={'console_scripts':['bridge = maicon_sim_bridge.bridge:main']})

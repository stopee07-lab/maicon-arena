#!/usr/bin/env python3
"""Reconstruct the demonstrated ROS notebook exercises, not organizer source files."""
from pathlib import Path
import hashlib
import json
import textwrap

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'workspace/examples'
DEST.mkdir(parents=True,exist_ok=True)
KERNEL={'display_name':'Python 3 (ROS 2 · simulation)','language':'python','name':'maicon-ros2'}
NOTE='> 3차 교육 영상 20–30분의 기능을 재구성한 **로컬 실습본**입니다. 공식 원본 노트북이 아닙니다. `mil_msgs` 원본 정의가 없어 이 환경에서는 `sim_mil_interfaces`를 사용합니다. 실제 ROS 2로 시뮬레이터와 통신합니다.'
COMMON='''import time
import math
import uuid
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions
from sim_mil_interfaces.msg import State as MissionState, Match as MatchCommand

# 상태/경기 토픽은 마지막 값을 새 구독자에게도 전달합니다.
STATUS_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.TRANSIENT_LOCAL)

def new_node(prefix):
    if not rclpy.ok():
        # Jupyter의 중단 버튼이 KeyboardInterrupt와 finally 정지를 처리하게 둡니다.
        rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
    return rclpy.create_node(prefix + '_' + uuid.uuid4().hex[:8])

def spin_until(node, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=min(0.05, max(0.0, deadline-time.monotonic())))
    if not predicate():
        raise TimeoutError('ROS 데이터/응답 시간 초과. 물리 경기장과 ROS 연결 상태를 확인하세요.')

def watch_status(node):
    data = {'state': None, 'match': None, 'state_at': None}
    def on_state(msg):
        data['state'] = msg
        data['state_at'] = time.monotonic()
    def on_match(msg):
        data['match'] = msg
    subscriptions = [
        node.create_subscription(MissionState, '/mil/state', on_state, STATUS_QOS),
        node.create_subscription(MatchCommand, '/mil/match', on_match, STATUS_QOS),
    ]
    spin_until(node, lambda: data['state'] is not None and data['match'] is not None)
    return data, subscriptions

def allowed(data):
    if data['state_at'] is None or time.monotonic()-data['state_at'] > 1.5:
        return False
    match = data['match']
    if match is None or match.cmd == 'pause':
        return False
    return match.mode == 'practice' or (match.mode == 'mission' and match.cmd in ('start', 'resume'))

def require_control(data):
    if not allowed(data):
        raise RuntimeError('제어 잠금 또는 상태 데이터 지연. 경기 재개·연결 상태를 확인하세요.')

def wait_publisher(node, publisher, timeout=5.0):
    spin_until(node, lambda: publisher.get_subscription_count() > 0, timeout)

def yaw_degrees(q):
    return math.degrees(math.atan2(2.0*(q.w*q.z + q.x*q.y), 1.0-2.0*(q.y*q.y+q.z*q.z)))

def source_age_seconds(node, message, maximum, label):
    stamp = message.header.stamp
    source_ns = int(stamp.sec)*1_000_000_000 + int(stamp.nanosec)
    if source_ns <= 0:
        raise ValueError(label + ': 원본 센서 시각이 없습니다.')
    age = (node.get_clock().now().nanoseconds-source_ns)/1_000_000_000
    if age < -0.25:
        raise ValueError(f'{label}: 센서 시각이 {-age:.3f}초 미래입니다. ROS 시계 설정을 확인하세요.')
    if age > maximum:
        raise TimeoutError(f'{label}: 원본 데이터가 {age:.3f}초 전 값입니다.')
    return age
'''


def md(source):return {'cell_type':'markdown','metadata':{},'source':textwrap.dedent(source).strip()+'\n'}
def code(source):return {'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':textwrap.dedent(source).strip()+'\n'}
def notebook(name,title,timestamp,description,settings,run,extra=''):
    cells=[md(f'# {title}\n\n영상 대응: **{timestamp}**\n\n{NOTE}\n\n{description}'),
           md('## 실행 방법\n\n위쪽 커널을 **Python 3 (ROS 2 · simulation)** 으로 선택하고, 아래 셀을 위에서부터 **Shift+Enter**로 실행합니다. 물리 경기장과 ROS 실습 연결을 먼저 켜세요. 각 실습은 시간이 지나면 종료되며, 셀을 다시 실행할 수 있습니다.'),
           code(COMMON),md('## 실습 설정\n\n아래 값을 바꾸고 다시 실행할 수 있습니다.'),code(settings),
           md('## 실행'),code(run)]
    if extra:cells.append(md(extra))
    cells.append(md('[← 실습 목차](00.실습목차.ipynb) · [코드 편집](../team_node.py)\n\n실물 장치의 토픽 타입·주기·응답과 맞는지는 지급 UGV에서 확인해야 합니다.'))
    data={'cells':cells,'metadata':{'kernelspec':KERNEL,'language_info':{'name':'python','version':'3.10'},'maicon_source':{'video':'maicon2026_mentoring03_20261106.mp4','interval':timestamp,'kind':'demonstration_reconstruction','official_original':False}},'nbformat':4,'nbformat_minor':5}
    for i,cell in enumerate(cells):cell['id']=hashlib.sha256(f'{name}-{i}'.encode()).hexdigest()[:12]
    (DEST/name).write_text(json.dumps(data,ensure_ascii=False,indent=1)+'\n')

notebook('01.GettingStarted.ipynb','01. UGV 로봇 개발 시작하기 (Getting Started)','20:00–21:59',
'''ROS 2는 로봇의 여러 프로그램이 데이터와 명령을 주고받도록 연결합니다.

| 개념 | 의미 | 이 실습의 예 |
|---|---|---|
| Node | ROS 2 통신에 참여하는 프로그램 단위 | 참가자 노드, 센서 브리지 |
| Topic | 데이터를 보내고 받는 통로 | `/mil/state`, `/image_raw`, `/cmd_vel` |
| Message | 토픽에서 주고받는 데이터 형식 | `MissionState`, `Twist`, `Image` |
| Service | 요청을 보내고 결과를 받는 방식 | `/mil/gimbal`, `/mil/fire` |

- **Subscribe**: 상태·경기 상태·카메라·IMU·위치를 수신합니다.
- **Publish**: 속도·OLED 문자열을 전송합니다. 별도 접수 응답은 없습니다.
- **Service**: 팬틸트·레이저 명령을 요청하고 응답을 확인합니다.
- **QoS**: 메시지 전달 방식입니다. 상태 토픽과 센서 토픽은 설정이 다릅니다.

현재 노트북은 Mac 안의 **Ubuntu 22.04 · ROS 2 Humble**에서 실행합니다. 로봇 하드웨어 대신 현재 3D 시뮬레이터에 연결합니다. Mac용 파일 편집·학습 커널과 구분합니다.

| 실습 | 기능 | 토픽/서비스 |
|---|---|---|
| 02.State | 체력·배터리·모드·경기 상태 | `/mil/state`, `/mil/match` |
| 03.Oled | 화면 4줄 문자열 | `/mil/oled` |
| 04.PanTilt | 좌우·상하 팬틸트 | `/mil/gimbal` |
| 05.Driving_Odometry | 속도 명령·출발점 기준 위치 | `/cmd_vel`, `/odom` |
| 06.Laser_Fire | 레이저 발사·대기시간 | `/mil/fire` |
| 07.Camera | 카메라 영상 한 장 | `/image_raw` |
| 08.Imu | 자세·위치 확인 | `/imu/data`, `/odom` |
''',
'DISCOVERY_SECONDS = 2.0',
'''import os
import platform
from IPython.display import display, Markdown
node = new_node('demo_getting_started')
try:
    end = time.monotonic() + DISCOVERY_SECONDS
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.05)
    topics = dict(node.get_topic_names_and_types())
    services = dict(node.get_service_names_and_types())
    expected_topics = ['/mil/state', '/mil/match', '/mil/oled', '/cmd_vel', '/odom', '/image_raw', '/imu/data']
    expected_services = ['/mil/gimbal', '/mil/fire']
    print('OS:', platform.system(), '| ROS:', os.environ.get('ROS_DISTRO'), '| Python:', platform.python_version())
    print('노트북 위치:', os.getcwd())
    rows = ['| 경로 | 실제 ROS 타입 |', '|---|---|']
    for topic in expected_topics:
        rows.append('| `' + topic + '` | ' + ', '.join(topics.get(topic, ['미발견'])) + ' |')
    for service in expected_services:
        rows.append('| `' + service + '` | ' + ', '.join(services.get(service, ['미발견'])) + ' |')
    display(Markdown('\\n'.join(rows)))
    missing = [t for t in expected_topics if t not in topics] + [s for s in expected_services if s not in services]
    DEMO_RESULT = {'demo':'01', 'ros_distro':os.environ.get('ROS_DISTRO'), 'topics':topics, 'services':services, 'missing':missing}
    if missing:
        print('연결 확인 필요:', ', '.join(missing))
    else:
        print('실습용 ROS 토픽·서비스 연결을 확인했습니다.')
finally:
    node.destroy_node()
''',
'''## 코드에서 연결되는 과정

`노트북의 rclpy 노드 → ROS 2 토픽/서비스 → 로봇 브리지 → 시뮬레이터`

공식 영상의 `mil_msgs/msg/MissionState`, `mil_msgs/msg/MatchCommand`는 이 실습에서 `sim_mil_interfaces/msg/State`, `sim_mil_interfaces/msg/Match`로 대응합니다. 코드 안의 이름을 `as MissionState`, `as MatchCommand`로 맞췄으며, 공식 패키지를 설치한 것으로 표시하지 않습니다.

JupyterLab은 코드를 쓰고 실행하는 작업 도구입니다. 노트북 실행 자체가 인식 모델 학습은 아닙니다.''')

notebook('02.State.ipynb','02. UGV 로봇 상태 확인 (State)','21:59–23:25',
'''`/mil/state`와 `/mil/match`를 구독해 체력, 배터리, 현재 모드와 경기 진행 상태를 확인합니다.

| 항목 | 영상 설명 |
|---|---|
| 로봇 상태 갱신 | 2Hz, 초당 2회 |
| 경기 상태 갱신 | 상태 변경 시 |
| 예제 관측 시간 | 10초, 변경 가능 |
| 주요 필드 | `hp`, `hp_max`, `battery_pct`, `mode`, `cmd` |

| 모드 | 체력·탄약 | 주행·조준·발사 |
|---|---|---|
| practice | 자동 회복 | 경기 단계 잠금 없음. 피격 잠금·재발사 대기는 적용 |
| mission | 자동 회복 없음 | 경기 진행 중 허용 |

`idle`, `standby`, `pause`, `report`, `stop`은 경기 제어 잠금 상태이며 `start`, `resume`은 진행 상태입니다. 아래 `control`은 수신한 모드와 상태를 이용해 코드에서 계산한 값입니다. 피격 잠금과 재발사 판정은 명령 응답도 확인해야 합니다.''',
'MONITOR_SECONDS = 10.0\nSTALE_SECONDS = 1.5',
'''node = new_node('demo_state')
DEMO_RESULT = {'demo':'02', 'samples':0}
try:
    data, subscriptions = watch_status(node)
    end = time.monotonic() + max(0.1, min(float(MONITOR_SECONDS), 60.0))
    next_print = 0.0
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.05)
        now = time.monotonic()
        if now < next_print:
            continue
        next_print = now + 0.5
        state, match = data['state'], data['match']
        age = now - data['state_at']
        if age > STALE_SECONDS:
            print(f'상태 지연: 마지막 수신 {age:.1f}초 전 · Locked')
            DEMO_RESULT.update(stale=True, age_seconds=age)
            continue
        control = 'Allowed' if allowed(data) else 'Locked'
        print(f'HP {state.hp}/{state.hp_max} | BAT {state.battery_pct:.0f}% | {match.mode} | {match.cmd} | {control}')
        DEMO_RESULT.update(samples=DEMO_RESULT['samples']+1, hp=int(state.hp), hp_max=int(state.hp_max),
                           battery_pct=float(state.battery_pct), mode=match.mode, cmd=match.cmd, control=control, stale=False)
finally:
    node.destroy_node()
''')

notebook('03.Oled.ipynb','03. UGV OLED 디스플레이 제어 (OLED Display)','23:27–23:47',
'''`/mil/oled`에 `OledLine` 메시지를 발행해 로봇의 화면에 문자열을 출력합니다.

| 항목 | 값 |
|---|---|
| 출력 줄 | 0~3, 총 4줄 |
| 문자열 | 한 줄 최대 21자, 영문·숫자·기호 |
| 전송 간격 | 0.5초 이상 |
| 화면 복귀 | 마지막 출력 약 10초 후 기본 화면 |

메시지를 발행하는 방식이므로 별도의 서비스 응답은 없습니다. 화면에서 실제 표시 결과를 확인합니다.''',
"TEAM_NAME = 'SANTA CLAUDE'\nDISPLAY_INTERVAL = 0.55",
'''from sim_mil_interfaces.msg import OledLine
node = new_node('demo_oled')
try:
    data, subscriptions = watch_status(node)
    state = data['state']
    lines = [TEAM_NAME, f'HP {state.hp}/{state.hp_max}', f'BAT {state.battery_pct:.0f}%', time.strftime('%H:%M:%S') + ' SEARCH']
    if any(not isinstance(text, str) or not text.isascii() or len(text) > 21 for text in lines):
        raise ValueError('OLED 문자열은 한 줄 21자 이하의 영문·숫자·기호여야 합니다.')
    publisher = node.create_publisher(OledLine, '/mil/oled', 10)
    wait_publisher(node, publisher)
    for line, text in enumerate(lines):
        msg = OledLine()
        msg.line = line
        msg.text = text
        publisher.publish(msg)
        print(f'line {line}: {text}')
        deadline = time.monotonic() + max(0.5, float(DISPLAY_INTERVAL))
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
    DEMO_RESULT = {'demo':'03', 'published_lines':lines, 'response':'토픽 발행 완료. 장치 표시 확인은 별도.'}
finally:
    node.destroy_node()
''')

notebook('04.PanTilt.ipynb','04. UGV 팬틸트 제어 (Pan Tilt)','23:47–24:34',
'''`/mil/gimbal` 서비스에 절대 위치를 요청합니다.

| 필드 | 의미 | 범위 |
|---|---|---|
| pan | 좌우, 양수는 오른쪽 | -100~100 |
| tilt | 상하, 양수는 위쪽 | -100~100 |
| speed | 이동 속도 | 1~270 °/s |
| acc | 가속도 설정값 | 1~254 |

`pan` 1단위는 0.5°, `tilt` 1단위는 0.15°입니다. 위치값을 각도 자체와 혼동하지 마세요.

`ok`는 **명령 접수**, `locked`는 제어 잠금, 응답 없음은 실패 또는 시간 초과입니다. `ok`가 실제 위치 도착을 의미하지 않습니다.''',
'PAN = 0.0\nTILT = 0.0\nSPEED = 80.0\nACC = 20.0\nTIMEOUT_SECONDS = 5.0',
'''from sim_mil_interfaces.srv import Gimbal
if not (-100 <= PAN <= 100 and -100 <= TILT <= 100 and 1 <= SPEED <= 270 and 1 <= ACC <= 254):
    raise ValueError('팬틸트 설정 범위를 확인하세요.')
node = new_node('demo_gimbal')
try:
    data, subscriptions = watch_status(node)
    require_control(data)
    client = node.create_client(Gimbal, '/mil/gimbal')
    if not client.wait_for_service(timeout_sec=TIMEOUT_SECONDS):
        raise TimeoutError('/mil/gimbal 서비스가 없습니다.')
    request = Gimbal.Request()
    request.pan, request.tilt = float(PAN), float(TILT)
    request.speed, request.acc = float(SPEED), float(ACC)
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=TIMEOUT_SECONDS)
    if not future.done():
        future.cancel()
        raise TimeoutError('팬틸트 응답 시간 초과')
    result = future.result()
    if result is None:
        raise RuntimeError('팬틸트 응답 없음')
    print('Gimbal:', result.message)
    print('요청 위치:', PAN, TILT, '| 각도:', PAN*0.5, TILT*0.15, 'deg')
    DEMO_RESULT = {'demo':'04', 'message':result.message, 'pan':PAN, 'tilt':TILT, 'arrival_verified':False}
finally:
    node.destroy_node()
''')

notebook('05.Driving_Odometry.ipynb','05. UGV 주행 및 위치 확인 (Driving And Odometry)','24:35–25:17',
'''`/cmd_vel`에 `geometry_msgs/msg/Twist`를 발행하고 `/odom`의 `nav_msgs/msg/Odometry`를 구독합니다.

| 항목 | 값 |
|---|---|
| 주행 명령 주기 | 10Hz, 초당 10회 |
| 위치 갱신 주기 | 교육자료 기준 20Hz |
| linear.x | 전진(+), 후진(-), -0.6~0.6 m/s |
| angular.z | 좌회전(+), 우회전(-), -3~3 rad/s |
| position.x / y | 출발점 기준 전후 / 좌우 위치(m) |
| orientation | 쿼터니언 x, y, z, w. yaw로 변환 가능 |

영상처럼 `RUN_DRIVE=False`가 기본입니다. 먼저 위치만 읽어 보고, 값을 True로 바꾸면 정해진 시간 동안 주행합니다. 센서가 늦거나 경기 제어가 잠기면 정지하며, 정상 종료·중단·오류에서도 0속도 명령을 보냅니다.''',
'RUN_DRIVE = False\nLINEAR_X = 0.10\nANGULAR_Z = 0.0\nDURATION_SECONDS = 2.0\nSTALE_SECONDS = 0.75',
'''from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
if not (-0.6 <= LINEAR_X <= 0.6 and -3.0 <= ANGULAR_Z <= 3.0):
    raise ValueError('주행 속도 범위를 확인하세요.')
if not 0.0 < float(DURATION_SECONDS) <= 10.0:
    raise ValueError('이 실습의 주행 시간은 0초 초과~10초입니다.')
node = new_node('demo_driving')
publisher = None
latest = {'odom':None, 'at':None}
def on_odom(msg):
    latest['odom'], latest['at'] = msg, time.monotonic()
try:
    subscription = node.create_subscription(Odometry, '/odom', on_odom, qos_profile_sensor_data)
    data, subscriptions = watch_status(node)
    spin_until(node, lambda: latest['odom'] is not None)
    odom_source_age = source_age_seconds(node, latest['odom'], STALE_SECONDS, 'Odometry')
    before = latest['odom'].pose.pose.position
    start_xy = [float(before.x), float(before.y)]
    if RUN_DRIVE:
        publisher = node.create_publisher(Twist, '/cmd_vel', 10)
        wait_publisher(node, publisher)
        end = time.monotonic() + float(DURATION_SECONDS)
        next_send = 0.0
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.02)
            require_control(data)
            if time.monotonic()-latest['at'] > STALE_SECONDS:
                raise TimeoutError('위치 데이터 지연: 주행을 멈춥니다.')
            odom_source_age = source_age_seconds(node, latest['odom'], STALE_SECONDS, 'Odometry')
            if time.monotonic() >= next_send:
                msg = Twist()
                msg.linear.x, msg.angular.z = float(LINEAR_X), float(ANGULAR_Z)
                publisher.publish(msg)
                next_send = time.monotonic() + 0.1
        publisher.publish(Twist())
        until = time.monotonic() + 0.25
        while time.monotonic() < until:
            rclpy.spin_once(node, timeout_sec=0.02)
    odom_source_age = source_age_seconds(node, latest['odom'], STALE_SECONDS, 'Odometry')
    position = latest['odom'].pose.pose
    yaw = yaw_degrees(position.orientation)
    print(f'Position: X={position.position.x:.3f}m, Y={position.position.y:.3f}m | Yaw={yaw:.1f}deg')
    print('주행 실행' if RUN_DRIVE else '위치 확인만 실행: RUN_DRIVE = False')
    DEMO_RESULT = {'demo':'05', 'drive_enabled':bool(RUN_DRIVE), 'start_xy':start_xy,
                   'end_xy':[float(position.position.x),float(position.position.y)], 'yaw_deg':yaw,
                   'source_age_seconds':odom_source_age,'received_age_seconds':time.monotonic()-latest['at']}
finally:
    if publisher is not None:
        for _ in range(3):
            publisher.publish(Twist())
            time.sleep(0.03)
        print('Stop command sent')
    node.destroy_node()
''')

notebook('06.Laser_Fire.ipynb','06. UGV 레이저 발사 (Laser Fire)','25:17–26:50',
'''`/mil/fire` 서비스로 레이저를 한 번 발사합니다. 발광은 5초, 다음 발사는 발사 시작 10초 후 가능합니다.

| 응답 message | 의미 |
|---|---|
| fired | 발사 성공 |
| empty | 탄약 없음 |
| locked | 발사 잠금 |
| reloading | 재발사 대기 |

`left_ms`는 다음 발사까지 남은 밀리초입니다. 영상에는 `success` 필드도 나오지만, 현재 시뮬레이션 타입에는 `message`, `left_ms`만 있습니다. 아래 성공값은 `message == 'fired'`로 계산합니다.

`RUN_FIRE=False`가 기본입니다. True로 바꿔 실행하고 곧바로 다시 실행하면 재발사 대기 응답을 확인할 수 있습니다. 실제 레이저 장비가 아닌 가상 경기장 명령입니다.''',
'RUN_FIRE = False\nTIMEOUT_SECONDS = 5.0',
'''from sim_mil_interfaces.srv import Fire
DEMO_RESULT = {'demo':'06', 'fire_enabled':bool(RUN_FIRE), 'message':'not_requested'}
if not RUN_FIRE:
    print('Set RUN_FIRE = True to fire')
else:
    node = new_node('demo_fire')
    try:
        data, subscriptions = watch_status(node)
        require_control(data)
        client = node.create_client(Fire, '/mil/fire')
        if not client.wait_for_service(timeout_sec=TIMEOUT_SECONDS):
            raise TimeoutError('/mil/fire 서비스가 없습니다.')
        future = client.call_async(Fire.Request())
        rclpy.spin_until_future_complete(node, future, timeout_sec=TIMEOUT_SECONDS)
        if not future.done():
            future.cancel()
            raise TimeoutError('레이저 발사 응답 시간 초과')
        result = future.result()
        if result is None:
            raise RuntimeError('레이저 발사 응답 없음')
        print('Fire:', result.message)
        print('Remaining:', result.left_ms, 'ms')
        DEMO_RESULT.update(message=result.message, left_ms=int(result.left_ms), success=result.message=='fired')
    finally:
        node.destroy_node()
''')

notebook('07.Camera.ipynb','07. UGV 카메라 영상 확인 (Camera)','26:51–28:14',
'''`/image_raw`의 `sensor_msgs/msg/Image`를 구독합니다. `cv_bridge`로 OpenCV 배열을 만든 뒤 JPEG로 변환하여 JupyterLab에 표시합니다.

| 항목 | 영상 설명 |
|---|---|
| 해상도 | 640 × 480 |
| 영상 형식 | BGR, OpenCV |
| 카메라 최대 프레임 | 공식 장치 최대 30 FPS, CPU 부하를 고려해 10 FPS 이하 권장 |
| 예제 출력 | 사진 한 장. 실시간 스트리밍 예제가 아님 |

현재 시뮬레이터의 실제 프레임 주기는 Mac·VM 부하에 따라 달라집니다. 센서가 없거나 영상이 늦으면 오류를 표시합니다. 깊이 영상 API는 이 실습에 포함하지 않습니다.''',
'TIMEOUT_SECONDS = 10.0\nMAX_FRAME_AGE_SECONDS = 2.0',
'''import cv2
from cv_bridge import CvBridge
from sensor_msgs.msg import Image as RosImage
from IPython.display import display, Image as NotebookImage
node = new_node('demo_camera')
latest = {'image':None, 'at':None}
def on_image(msg):
    latest['image'], latest['at'] = msg, time.monotonic()
try:
    subscription = node.create_subscription(RosImage, '/image_raw', on_image, qos_profile_sensor_data)
    spin_until(node, lambda: latest['image'] is not None, TIMEOUT_SECONDS)
    msg = latest['image']
    age = time.monotonic()-latest['at']
    if age > MAX_FRAME_AGE_SECONDS:
        raise TimeoutError('카메라 수신 데이터가 오래됐습니다.')
    source_age = source_age_seconds(node, msg, MAX_FRAME_AGE_SECONDS, 'Camera')
    if not msg.data or msg.width <= 0 or msg.height <= 0:
        raise ValueError('비어 있거나 잘못된 카메라 프레임입니다.')
    frame = CvBridge().imgmsg_to_cv2(msg, desired_encoding='bgr8')
    ok, jpg = cv2.imencode('.jpg', frame)
    if not ok:
        raise RuntimeError('카메라 JPEG 변환 실패')
    print('Camera:', frame.shape, '| source:', msg.encoding)
    display(NotebookImage(data=jpg.tobytes(), width=640))
    DEMO_RESULT = {'demo':'07','shape':list(frame.shape),'encoding':msg.encoding,'bytes':len(jpg),
                   'received_age_seconds':age,'source_age_seconds':source_age,'frame_id':msg.header.frame_id,'source_stamp':{'sec':msg.header.stamp.sec,'nanosec':msg.header.stamp.nanosec}}
finally:
    node.destroy_node()
''')

notebook('08.Imu.ipynb','08. UGV IMU 및 위치 확인 (IMU)','28:14–30:00',
'''`/imu/data`의 `sensor_msgs/msg/Imu`와 `/odom`의 `nav_msgs/msg/Odometry`를 구독합니다. 영상 기준 갱신 주기는 20Hz입니다.

| 값 | 의미 |
|---|---|
| roll | 좌우 기울기(°) |
| pitch | 앞뒤 기울기(°) |
| yaw | 로봇 회전 방향(°) |
| linear_acceleration | 가속도(m/s²) |
| g | 중력가속도 기준 크기 |
| position.x / y | 출발점 기준 전후 / 좌우 위치(m) |

영상 예제는 센서를 읽은 뒤 0속도 명령도 보냅니다. 현재 시뮬레이터는 yaw 기반의 이상적인 자세를 제공하며 가속도·각속도는 제공하지 않습니다(`covariance[0] == -1`). 따라서 가속도를 0g 또는 1g로 꾸며 표시하지 않습니다.

서비스 응답 대기에는 시간 제한을 두고, 센서가 없거나 오래된 경우를 구분해야 한다는 영상 설명을 실습 코드에 적용했습니다.''',
'TIMEOUT_SECONDS = 5.0\nMAX_SENSOR_AGE_SECONDS = 0.75\nSEND_STOP = True',
'''from sensor_msgs.msg import Imu
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
node = new_node('demo_imu')
latest = {'imu':None,'odom':None,'imu_at':None,'odom_at':None}
publisher = None
def on_imu(msg):
    latest['imu'], latest['imu_at'] = msg, time.monotonic()
def on_odom(msg):
    latest['odom'], latest['odom_at'] = msg, time.monotonic()
try:
    subscriptions = [node.create_subscription(Imu, '/imu/data', on_imu, qos_profile_sensor_data),
                     node.create_subscription(Odometry, '/odom', on_odom, qos_profile_sensor_data)]
    spin_until(node, lambda: latest['imu'] is not None and latest['odom'] is not None, TIMEOUT_SECONDS)
    now = time.monotonic()
    if any(now-latest[key] > MAX_SENSOR_AGE_SECONDS for key in ('imu_at','odom_at')):
        raise TimeoutError('IMU 또는 위치 데이터 지연')
    source_ages = {key:source_age_seconds(node, latest[key], MAX_SENSOR_AGE_SECONDS, key) for key in ('imu','odom')}
    imu = latest['imu'];q = imu.orientation
    if imu.orientation_covariance[0] == -1:
        raise RuntimeError('자세 데이터를 제공하지 않는 IMU 메시지입니다.')
    roll = math.degrees(math.atan2(2*(q.w*q.x+q.y*q.z), 1-2*(q.x*q.x+q.y*q.y)))
    pitch = math.degrees(math.asin(max(-1.0,min(1.0,2*(q.w*q.y-q.z*q.x)))))
    yaw = yaw_degrees(q)
    position = latest['odom'].pose.pose.position
    acceleration_available = bool(imu.linear_acceleration_covariance[0] != -1)
    acceleration_g = math.sqrt(imu.linear_acceleration.x**2+imu.linear_acceleration.y**2+imu.linear_acceleration.z**2)/9.80665 if acceleration_available else None
    print(f'Roll: {roll:.1f} | Pitch: {pitch:.1f} | Yaw: {yaw:.1f} | X: {position.x:.3f} | Y: {position.y:.3f}')
    print(f'Acc: {acceleration_g:.2f}g' if acceleration_available else 'Acc: 미제공 (시뮬레이션 IMU)')
    if SEND_STOP:
        publisher = node.create_publisher(Twist, '/cmd_vel', 10)
        wait_publisher(node, publisher)
        publisher.publish(Twist())
        time.sleep(0.1)
        print('Stop command sent')
    DEMO_RESULT = {'demo':'08','roll_deg':roll,'pitch_deg':pitch,'yaw_deg':yaw,
                   'xy':[float(position.x),float(position.y)],'acceleration_available':acceleration_available,
                   'acceleration_g':acceleration_g,'stop_sent':bool(SEND_STOP),
                   'source_age_seconds':source_ages,'received_age_seconds':{key:now-latest[key+'_at'] for key in ('imu','odom')}}
finally:
    if publisher is not None:
        publisher.publish(Twist())
        time.sleep(0.03)
    node.destroy_node()
''')

names=[('01.GettingStarted.ipynb','개발 시작 · ROS 연결 확인'),('02.State.ipynb','체력·배터리·경기 상태'),('03.Oled.ipynb','OLED 문자열 출력'),('04.PanTilt.ipynb','팬틸트 서비스'),('05.Driving_Odometry.ipynb','주행 명령·위치'),('06.Laser_Fire.ipynb','레이저 서비스·재발사 대기'),('07.Camera.ipynb','카메라 사진 한 장'),('08.Imu.ipynb','IMU·위치·정지 명령')]
index=[md('# 3차 교육 영상 · ROS 2 실습\n\n'+NOTE+'\n\n영상 **20:00–30:00**에 나온 8개 예제와 같은 순서입니다. 아래 파일을 열고 위에서부터 **Shift+Enter**로 실행하세요.\n\n**커널: Python 3 (ROS 2 · simulation)**\n\n'+ '\n'.join(f'{i}. [{label}]({name})' for i,(name,label) in enumerate(names,1))),
md('## 시작 순서\n\n1. 개발 화면에서 물리 경기장을 자유연습으로 켜고, 임무 주행 프로세스를 정지합니다.\n2. **영상 실습 · ROS 2**를 엽니다. 이 창은 Ubuntu 22.04·ROS 2 Humble 커널을 사용합니다.\n3. 01번에서 연결을 확인한 뒤 원하는 예제를 엽니다.\n4. 주행은 `RUN_DRIVE`, 발사는 `RUN_FIRE`를 직접 True로 바꾼 경우에 실행합니다.\n5. 셀을 중단하려면 위쪽 ■ 버튼 또는 Kernel → Interrupt Kernel을 사용합니다.\n\n로봇 자체의 주행 알고리즘은 바꾸지 않았습니다. 각 노트북은 센서·명령을 직접 시험하는 별도 실습입니다.'),
md('## 영상과 현재 환경의 차이\n\n| 항목 | 영상 | 현재 실습 |\n|---|---|---|\n| 실행 | 실제 UGV의 JupyterLab | Mac 안의 Ubuntu·ROS 2 JupyterLab |\n| 로봇 | 지급 UGV | 3D 시뮬레이터 B-1 |\n| 사용자 정의 메시지 | `mil_msgs` | `sim_mil_interfaces` |\n| 카메라 | 최대 30FPS, 640×480 BGR | 640×480 BGR, 실제 주기는 실행 부하에 따름 |\n| IMU 가속도 | 실물 IMU 값 | 미제공으로 표시 |\n\n노트북을 저장하면 프로젝트 `simulator/workspace/examples`에 저장됩니다. 참가자 실행 코드 파일은 `simulator/workspace/team_node.py`입니다. 노트북 수정과 실행 중인 참가자 코드 버전 적용은 별개입니다.')]
for i,c in enumerate(index):c['id']=f'index-{i}'
(DEST/'00.실습목차.ipynb').write_text(json.dumps({'cells':index,'metadata':{'kernelspec':KERNEL,'language_info':{'name':'python','version':'3.10'}},'nbformat':4,'nbformat_minor':5},ensure_ascii=False,indent=1)+'\n')

if __name__=='__main__':
    import ast
    for p in sorted(DEST.glob('*.ipynb')):
        nb=json.loads(p.read_text())
        for cell in nb['cells']:
            if cell['cell_type']=='code':ast.parse(cell['source'],filename=p.name)
        print(p.name, len(nb['cells']), 'cells; Python syntax OK')

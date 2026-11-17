# Mac · Windows · Linux 실행

같은 소스코드를 사용합니다. 각 컴퓨터에서 Python과 필요한 패키지를 새로 설치합니다. Mac에서 만든 `training/.venv` 폴더는 복사하지 않습니다.

## 내 컴퓨터에서 시작

1. **Python 3.12 (64비트)**를 설치합니다. Windows에서는 **Add python.exe to PATH**를 체크합니다.
2. Git에서 받은 폴더의 압축을 풉니다.
3. **Windows: `start.cmd` / Mac: `start.command`**를 엽니다. 첫 실행은 설치 때문에 몇 분 걸립니다.
4. 처음에는 코드·학습 → 코드 수정에서 경기/인식 + 행동 코드/제공 모델을 선택하고 현재 코드 적용·실행을 누릅니다. 이후에는 경기 화면의 코드·경기장 실행을 사용합니다. 검은 실행 창은 켜 둡니다.
5. 끝낼 때 실행 중인 터미널 창을 닫습니다. **Ctrl+C**로도 종료할 수 있습니다.

Mac에서 더블클릭이 안 되면 터미널에서 해당 폴더로 이동해 `bash start.command`를 실행합니다. 설치는 인터넷이 필요합니다. 이후 경기장에는 로컬 주소로 접속합니다.

| 환경 | 실행 | 렌더링 및 현재 검증 범위 |
| --- | --- | --- |
| Mac Apple Silicon | `bash start.command` | 기본 MuJoCo 렌더러. 로컬 Mac에서 기존 환경으로 실제 실행 검증 |
| Windows x86_64 | `start.cmd` | MuJoCo OpenGL. 그래픽 제조사 드라이버가 필요하며 CI에서는 별도 Mesa 소프트웨어 렌더러를 사용합니다. 최신 실행 결과는 아래 검증 기록 참조 |
| Linux 서버 | `bash start.sh --no-browser` | 화면 없는 서버는 OSMesa 소프트웨어 렌더링. ARM64 컨테이너 새 설치·카메라 및 x86_64 CI 실제 경기 실행 확인. GPU 없는 작은 서버는 실시간보다 느릴 수 있음 |

Python 3.11 이상을 허용하며 새 설치는 3.12를 권장합니다. 실행기는 컴퓨터에 있는 Python으로 별도 가상환경을 만듭니다. OS별 라이브러리를 담는 곳만 Windows `Scripts/python.exe`, Mac/Linux `bin/python`으로 달라집니다. Python 설치 경로에 공백·한글이 있어도 인수를 나누어 실행합니다.

2026-11-16 Mac 확인 환경: Python 3.14.5 / MuJoCo 3.17.0 / Torch 2.15.0. 기존 가상환경을 명시적으로 선택해 실제 카메라, 새 서버 시작, 물리 프레임 수신, 정상 종료와 Jupyter 시작·종료를 확인했습니다. 이 결과는 새 Mac 설치나 Windows 실행을 대신하지 않습니다. Intel Mac과 Windows ARM64의 패키지 호환성은 확인하지 않았습니다.

같은 날 Linux ARM64의 깨끗한 `python:3.12-slim` 컨테이너에 시스템 렌더링 라이브러리와 새 `requirements.txt`를 설치했습니다. Python 3.12.16 / MuJoCo 3.17.0 / Torch 2.15.0+cpu 환경에서 이식성 테스트 11개와 OSMesa 실제 카메라 렌더링이 통과했습니다. 컨테이너는 검증 후 제거했습니다. 이 내용은 11월 16일 당시 기록입니다. 11월 17일의 OS별 실제 실행 결과는 아래 검증 기록에서 구분합니다.

## Ubuntu 클라우드 서버

서버에서 이 저장소 폴더를 연 뒤 실행합니다.

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv libosmesa6 libgl1 libglib2.0-0
bash start.sh --no-browser
```

프로그램은 서버의 `127.0.0.1:8887`에서 실행됩니다. 내 컴퓨터의 터미널/PowerShell에서 다음 연결을 켭니다. `사용자@서버주소`는 실제 SSH 접속 정보로 바꿉니다.

```bash
ssh -L 8887:127.0.0.1:8887 사용자@서버주소
```

내 컴퓨터 브라우저로 **http://127.0.0.1:8887/?page=run**을 엽니다. 서버 실행 창과 SSH 창을 모두 켜 둡니다. 경기장 포트를 인터넷에 공개하거나 `0.0.0.0`으로 바꿀 필요가 없습니다. 이 앱은 로컬 사용자용 코드 실행 기능이 있으므로 SSH로 접속합니다.

8887번이 사용 중이면 서버에서 `bash start.sh --no-browser --port 8897`, 내 컴퓨터에서 `ssh -L 8897:127.0.0.1:8897 사용자@서버주소`로 양쪽 번호를 함께 바꾸고 8897 주소로 접속합니다.

## 설치 확인과 선택 기능

```bash
python launch.py --check
```

Mac/Linux에서 `python` 명령이 없으면 `python3`를 사용합니다. 이 검사는 패키지 import와 **실제 MuJoCo 64×64 카메라 렌더링**을 수행합니다. 파일이 있다는 것만으로 성공 판정하지 않습니다.

- **학습:** 기본 CPU는 공통입니다. Linux/Windows 설치기는 [공식 PyTorch의 CPU 패키지](https://pytorch.org/get-started/locally/)를 먼저 설치해 불필요한 CUDA 다운로드를 줄입니다. Mac의 MPS는 지원되는 기기에서 선택합니다. Windows/Linux GPU 학습용 CUDA는 별도 PyTorch 설치가 필요하며 현재 화면의 장치 선택은 CPU/MPS입니다.
- **JupyterLab 편집기:** 기본 실행에는 필요 없습니다. `python launch.py --setup --notebook`으로 추가 설치합니다. 서버에서 사용할 때는 SSH 명령에 `-L 8907:127.0.0.1:8907`도 추가합니다. 브라우저 편집기 주소의 접근 토큰은 공유하지 않습니다.
- **Linux GPU 렌더링:** 드라이버가 준비된 환경은 `MUJOCO_GL=egl bash start.sh --no-browser`로 직접 선택할 수 있습니다. 자동 GPU 설정이나 CUDA 검증은 하지 않습니다. 디스플레이가 없는 Linux의 기본값은 `osmesa`입니다.
- **ROS 실습:** 기존 `ros2/mac-runtime.sh`는 Mac의 Colima/Docker용입니다. 이번 공통 설치는 ROS를 설치하지 않습니다. Windows/Linux의 ROS 컨테이너 실행은 아직 지원 검증 전이며 기본 경기장·코드 실행과 구분합니다.
- **녹화:** 브라우저 녹화는 WEBM으로 저장할 수 있습니다. MP4 변환에는 별도 FFmpeg가 필요합니다.

## 팀 공유 시 포함할 것

소스, `requirements.txt`, 시작 파일, 문서와 저장소에 포함된 예제 모델을 공유합니다. 실행 기록·접속 키·가상환경은 `.gitignore`로 제외됩니다. 팀원별로 설치하면 각 OS에 맞는 패키지가 내려옵니다. `requirements.lock.txt`는 이전 Mac 환경의 기록이며 공통 설치에 사용하지 않습니다. 새 설치의 실제 패키지 버전은 `runtime/installed-packages-운영체제.txt`에 남깁니다.

OS별 실행 CI는 `.github/workflows/platform-smoke.yml`에 있습니다. Windows/Linux는 새 설치, 실제 카메라, 제공 모델 로드, 참가 로봇 이동, 배속 선택, 일시정지·재개·종료를 검사합니다. GitHub의 Mac ARM 서버는 CGL 그래픽 장치가 없으므로 설치·import·이식성 검사만 수행하며, 실제 Mac 렌더링은 로컬 검증 기록으로 구분합니다. 팀원 PC의 그래픽 드라이버·브라우저와 실제 UGV 검증은 포함하지 않습니다.

개발자가 기존 환경으로 검사해야 할 때만 `MAICON_PYTHON`에 Python 실행 파일의 경로를 지정할 수 있습니다. 일반 팀원은 설정할 필요가 없습니다. 이 설정이 켜져 있을 때 설치기는 다른 환경을 변경하지 않고 중단합니다.

## 2026-11-17 검증 기록

기계가 읽을 수 있는 환경·검사·실패 이력은 [platform-verification.json](platform-verification.json)에 있습니다.

Windows에서 `WGL` 또는 `gladLoadGL` 오류가 나면 그래픽 제조사의 드라이버와 OpenGL 지원을 확인하세요. 원격 데스크톱·그래픽 장치 없는 서버 환경은 일반 데스크톱과 다를 수 있습니다. CI용 Mesa 설치는 임시 검증 서버에만 적용하며 배포 시작 파일은 시스템 그래픽 드라이버나 레지스트리를 변경하지 않습니다.

CPU 소프트웨어 렌더링은 컴퓨터 성능에 따라 1×보다 느릴 수 있습니다. 이전 Windows llvmpipe 조건은 약 0.03×(벽시계 300초 동안 모의 시간 7.75초)로 느려 참가 로봇 이동 검사에 실패했습니다. 최종 검사는 별도 조건인 GLonD3D12 + Microsoft WARP에서 통과했습니다. 일반 PC의 GPU 속도를 검증한 결과가 아닙니다. 화면의 실제 배속을 확인하세요. 5×/20× 선택은 목표값이며 달성 속도 보장이 아닙니다.

Windows 이식 과정에서 코드 저장 시 줄바꿈 변환으로 무결성 검증이 실패하는 문제를 수정했습니다. 서버 종료 감시용 입력 파이프가 자식 작업자에 상속되어 Python 시작 전에 멈추는 현상도 재현해 입력을 분리했습니다. 관련 동작은 [CPython 이슈 78961](https://github.com/python/cpython/issues/78961)에 설명되어 있습니다. 참가 코드 종료는 OS 공통 사용자 확인과 기록된 자식 프로세스의 생성 시각·명령·배포 경로를 대조합니다.

Windows 파일 공유 잠금과 겹친 상태·카메라 파일 갱신에는 최대 180ms의 제한된 재시도를 적용합니다. 지속적인 쓰기 권한 오류는 숨기지 않고 실패로 처리하며 기존 완전한 상태 파일을 보존합니다.

[최종 자동 검증](https://github.com/stopee07-lab/maicon-arena/actions/runs/43987978341)은 Windows x86_64와 Linux x86_64에서 모두 통과했습니다. 새 설치·카메라·제공 모델·8.1초 모의 경기의 참가 로봇 이동·배속 선택·일시정지·재개·명시적 종료를 검사했습니다. 5분 전체 경기나 팀원 PC의 브라우저·그래픽 드라이버를 검증한 결과는 아닙니다. Windows는 [Mesa GLonD3D12의 기본 Microsoft WARP](https://github.com/pal1000/mesa-dist-win/blob/master/readme.md)를 사용했으며, 실패한 llvmpipe 조건과 원시 기록을 구분해 보존했습니다.

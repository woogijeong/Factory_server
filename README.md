# GX Simulator2 ↔ Factory I/O OPC UA 브리지

미쓰비시 **GX Works2 / GX Simulator2**의 PLC 디바이스(X, Y, M, D)를 **Factory I/O**와 OPC UA로 연결하는 Python 프로그램입니다.

```
Factory I/O ──(OPC UA 클라이언트)──▶ bridge.py (OPC UA 서버) ──(MX Component)──▶ GX Simulator2
   센서·액추에이터                       opc.tcp://127.0.0.1:4840                  래더 프로그램
```

- Factory I/O가 쓰는 값(센서)은 PLC로 보내고, PLC가 내보내는 값(모터·램프)은 Factory I/O로 보냅니다.
- 어떤 PLC 주소를 어떤 태그로 노출할지는 `tags.yaml`에서 정합니다. 코드를 고칠 필요가 없습니다.
- PLC 없이 시험할 수 있는 모의(mock) PLC 모드와 테스트가 들어 있습니다.

---

## 1. 실행 요구사항

| 구분 | 요구사항 | 비고 |
|---|---|---|
| OS | **Windows** | MX Component가 Windows COM 구성요소입니다. |
| 미쓰비시 소프트웨어 | **GX Works2** (GX Simulator2 포함) | 확인한 버전: 1.612 |
| 미쓰비시 소프트웨어 | **MX Component** (**32비트** COM `ActUtlType`) | **별도 라이선스 제품**이며 GX Works2에 포함되지 않습니다. 버전은 확인하지 못했습니다. |
| Factory I/O | **OPC Client DA/UA 드라이버**가 있는 에디션 (Modbus & OPC 또는 Ultimate) | 확인한 버전: 2.5.10 |
| Python 환경 | [**uv**](https://docs.astral.sh/uv/) | `setup.bat`이 32비트 Python 3.12와 패키지를 설치합니다. |
| Python 패키지 | `asyncua`, `cryptography<50`, `comtypes`, `PyYAML` | `requirements.txt` 참고. 확인한 `asyncua` 버전은 2.1.0입니다. |

**왜 32비트 Python인가:** MX Component의 `ActUtlType`은 32비트 프로세스 안에서 도는 COM DLL이라 64비트 Python에서는 불러올 수 없습니다. 64비트용(`ActUtlType64`)은 GX Simulator2에 접속되지 않는다는 공개 보고가 있어 사용하지 않았고, 직접 시험하지도 않았습니다.

**왜 `cryptography<50`인가:** 최신 50.x에는 32비트 Windows용 휠이 없어 소스 빌드(Rust)가 필요하고, 그 빌드가 실패합니다.

**왜 SLMP가 아닌가:** GX Simulator2는 SLMP 통신을 지원하지 않는다고 알려져 있어(Emulate3D 문서) MX Component 방식을 택했습니다.

---

## 2. 설치 (새 PC에서 처음 한 번)

1. **MX Component 논리국 설정** (`setup.bat`이 대신 해 주지 못합니다)
   - MX Component의 *Communication Setup Utility* → 논리국 번호 **1** 생성
   - PC 쪽 I/F = **GX Simulator2**, CPU 종류 = GX Works2 프로젝트의 CPU와 동일하게
   - **Connection Test**가 성공해야 합니다 (시뮬레이션이 켜져 있어야 시험할 수 있습니다).
   - 다른 번호를 쓰면 `tags.yaml`의 `plc.station`을 같은 값으로 바꿉니다.
2. `setup.bat` 더블클릭
   - 32비트 Python 3.12 설치, `.venv` 생성, 패키지 설치, MX Component 등록 여부 확인을 합니다.
   - 수동으로 하려면:
     ```
     uv python install cpython-3.12-windows-x86
     uv venv .venv --python cpython-3.12-windows-x86
     uv pip install --python .venv\Scripts\python.exe --only-binary cryptography -r requirements.txt
     ```
3. 폴더를 다른 PC로 복사할 때는 **`.venv`를 빼고** 복사한 뒤 그 PC에서 `setup.bat`을 실행합니다. `.venv`는 원래 PC의 경로에 묶여 있어서 옮기면 동작하지 않습니다.

---

## 3. 사용 방법

### 3-1. 매번 쓰는 순서

1. **GX Works2**에서 시뮬레이션을 시작합니다 (Debug → Start/Stop Simulation). 래더가 시뮬레이터에 써져 있어야 합니다.
2. **`start.bat`** 더블클릭. 아래 메시지가 나오면 준비된 것입니다.
   ```
   OPC UA server on opc.tcp://127.0.0.1:4840, 61 tags
   ```
   PLC 연결이 막혀 있으면 `PLC open failed ... retrying`이 나오며 최대 약 2분간 재시도합니다.
3. **Factory I/O**에서 연결합니다 (3-3 참고).
4. 끌 때는 서버 창에서 **Ctrl+C**를 누릅니다. (창의 X 버튼은 4-1 참고)

### 3-2. 태그 설정 (`tags.yaml`)

태그 이름은 PLC 주소와 같습니다. 부품 이름은 Factory I/O에서 붙입니다.

```yaml
tags:
  - {name: X0,   address: X0,   type: bit,  direction: to_plc}     # Factory I/O → PLC
  - {name: Y20,  address: Y20,  type: bit,  direction: from_plc}   # PLC → Factory I/O
  - {name: D100, address: D100, type: word, direction: to_plc}
  - {name: D110, address: D110, type: word, direction: from_plc}
```

현재 기본 설정은 61개입니다.

| 범위 | 방향 | 타입 |
|---|---|---|
| X0~X19 | `to_plc` (Factory I/O → PLC) | 비트 |
| Y20~Y39 | `from_plc` (PLC → Factory I/O) | 비트 |
| D100~D109 | `to_plc` | 워드 (Int32 노드, PLC 쪽은 int16) |
| D110~D120 | `from_plc` | 워드 |

| 항목 | 규칙 |
|---|---|
| `name` | 영문·숫자·`_`만, 중복 불가 |
| `address` | `X`, `Y`, `M`(비트)와 `D`(워드). 같은 주소를 두 태그에 쓸 수 없음 |
| `type` | `X`, `Y`, `M`은 `bit`, `D`는 `word`. 안 맞으면 시작할 때 태그 이름과 함께 거부 |
| `direction` | `to_plc` 또는 `from_plc`. `both`는 지원하지 않음 |

- **방향은 브리지가 정합니다.** Factory I/O는 노드를 어느 쪽에든 이을 수 있지만, 브리지는 `to_plc` 노드만 PLC에 쓰고 `from_plc` 노드는 PLC에서 읽어 채우며 읽기 전용으로 둡니다. 한 주소의 값을 쓰는 쪽이 하나라서 값이 서로 덮어쓰이지 않습니다.
- 태그를 바꾸면 서버를 껐다 켜고, Factory I/O에서 **Browse**를 다시 눌러야 합니다.
- 모의 PLC로 쓰려면 `plc.driver: mock`으로 바꿉니다. `plc.ladder: {Y20: X0}`처럼 쓰면 모의 PLC가 `X0` 값을 `Y20`으로 복사합니다.

### 3-3. Factory I/O 연결

1. Drivers에서 **OPC Client DA/UA**를 선택하고 **CONFIGURATION**을 엽니다.
2. 서버 주소: `opc.tcp://127.0.0.1:4840`
3. Items/Nodes 설정:

   | 항목 | 값 |
   |---|---|
   | Filter names that start with | 비워 둠 |
   | Filter names that contain | `;s=` |
   | Limit | 256 |

   노드 ID가 `ns=2;s=X0` 형태라서 `;s=`로 우리 노드만 걸러집니다. 필터를 비우면 서버 기본 노드가 Limit을 채워 우리 노드가 밀리고, 옛 필터(예: `FIO`)가 남아 있으면 모든 노드가 사라집니다.
4. 드라이버 화면에서 **Browse**를 누르면 가운데에 노드가 나옵니다.
5. **드래그로 연결**합니다.
   - 센서의 네모 칸(왼쪽 열) → `X…` / `D100…D109` 노드 **왼쪽** 흰 칸 (Factory I/O → PLC)
   - `Y…` / `D110…D120` 노드 **오른쪽** 흰 칸 → 액추에이터의 네모 칸(오른쪽 열) (PLC → Factory I/O)
6. 장면을 Run 상태로 두면 동작합니다.

### 3-4. 래더 예시

센서 X0이 켜지면 컨베이어 Y20이 켜지는 한 줄입니다.

```
|--[ X0 ]--------------( Y20 )--|
```

GX Works2에서 프로그램을 쓰고 시뮬레이터에 쓴 뒤 시뮬레이션을 시작합니다. (이 예시 그대로의 조합은 시험하지 않았고, `X0 → Y2` 래더로 같은 경로를 시험했습니다.)

---

## 4. 주의할 점

### 4-1. 종료는 반드시 Ctrl+C로
창의 X 버튼이나 프로세스 강제 종료는 MX 연결을 정리하지 못합니다. 그러면 다음 시작에서 `MX Open failed: 0x01801007`이 **30~60초 정도** 이어질 수 있습니다. `start.bat`은 이를 알고 최대 약 2분간 재시도합니다.

### 4-2. PLC 연결은 하나만
같은 시뮬레이터에 MX 연결을 동시에 여러 개 열면 서로 느려집니다. 시뮬레이터가 요청을 하나씩 처리하기 때문입니다. 측정 결과 연결이 2개면 호출당 약 78ms, 3개면 약 111ms였습니다. 브리지가 돌 때 다른 MX 프로그램을 같이 쓰지 마세요.

### 4-3. 응답 속도
- MX 호출은 호출당 약 38ms이고, 이 시간은 GX Simulator2 안에서 쓰입니다. 스레드·연결을 늘리거나 타이머 해상도를 올려도 줄지 않았습니다.
- 그래서 `to_plc` 값은 **바뀔 때만** PLC에 쓰고(1초마다 한 번은 전부 다시 씀), `from_plc` 값은 매 주기 읽습니다.
- 센서 변화 → 래더 → 모터 변화까지의 왕복은 **중앙값 약 126ms, p95 약 143~172ms**였습니다 (OPC UA 클라이언트 기준, 시뮬레이터 스캔 시간 포함, 20회씩 측정). Factory I/O 화면 자체의 반응 시간은 재지 않았습니다.
- 짧은 펄스(약 100ms 이하)는 놓칠 수 있습니다. 센서는 수준(level) 신호라 데모에는 문제없었습니다.

### 4-4. PLC 주소
- **X, Y 주소는 16진수**입니다(Q/L 계열). `X0~X9` 다음이 `X10`이지만, 그 사이 `XA~XF`가 있습니다. GX Works2의 입출력 할당과 맞는지 확인하세요.
- **X(입력)에 쓰기:** 지금 환경에서는 한 번 쓴 값이 15초 이상 유지되고 래더에 전달됐습니다. 다만 처음 시험에서는 0.5초 뒤 지워졌고 그 원인을 모릅니다. 1초 갱신이 다시 써 주지만 래더에 순간 공백이 보일 수 있습니다. 불안하면 `M` 디바이스를 센서에 쓰세요.
- 프로젝트의 래더가 건드리는 `D` 주소는 피하세요. 이 환경의 프로젝트는 `D0`, `D1`을 썼습니다.

### 4-5. `to_plc` 주소는 덮어써집니다
GX Works2의 디바이스 모니터에서 `to_plc` 주소를 손으로 바꿔도 1초 안에 Factory I/O 값으로 되돌아갑니다. Factory I/O가 그 주소의 주인이기 때문입니다.

### 4-6. 시뮬레이터가 멈추면
브리지는 오류를 로그에 남기고 1초마다 재연결하며, 그동안 **마지막 값을 유지**합니다. Factory I/O의 액추에이터도 마지막 상태로 멈춰 있게 됩니다(컨베이어가 돌던 상태면 계속 돕니다).

### 4-7. 보안과 접속 범위
- 서버는 **익명 접속**을 허용하고 `127.0.0.1`에서만 듣습니다. 같은 PC의 Factory I/O만 접속할 수 있습니다.
- 다른 PC에서 접속하려면 `tags.yaml`의 `opcua.endpoint`를 이 PC의 IP로 바꾸고(`0.0.0.0`은 안 됨: 서버가 설정한 주소를 그대로 클라이언트에 알려 줍니다) 방화벽에서 4840 포트를 열어야 합니다. 두 PC 구성은 시험하지 않았습니다.
- 서버는 보안 없음과 Basic256Sha256(서명, 서명+암호화)을 함께 엽니다. Factory I/O가 서버 인증서가 없으면 연결하지 못해서 자체 서명 인증서를 `certs/`에 만듭니다. **`certs/server_key.pem`은 개인 키이니 저장소에 올리거나 공유하지 마세요** (`.gitignore`에 들어 있습니다).
- 쓰기 가능한 PLC 디바이스를 인증 없이 노출하는 구조입니다. 신뢰할 수 있는 로컬 환경에서만 쓰세요.

### 4-8. 개수 한계
Factory I/O는 노드를 최대 256개까지 Browse합니다. 태그 수가 늘어도 MX 호출 횟수는 같지만(배치 호출), 많은 태그로 응답 속도를 재 본 적은 없습니다.

---

## 5. 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `MX Open failed: 0x01801007` | 시뮬레이션이 꺼져 있거나 논리국이 잘못됨. 또는 직전에 프로그램을 강제 종료해 연결이 아직 풀리지 않음(30~60초). 시뮬레이션을 시작하고 기다리세요. |
| `ModuleNotFoundError` 또는 `.venv`가 동작 안 함 | 다른 PC에서 `.venv`를 복사한 경우입니다. `setup.bat`을 실행하세요. |
| `cryptography` 설치 중 Rust/maturin 빌드 실패 | 32비트 Windows용 휠이 없는 버전입니다. `--only-binary cryptography`와 `cryptography<50`이 필요합니다(`setup.bat`은 적용돼 있음). |
| Factory I/O: `server did not return the certificate used to create the secure channel` | 서버에 인증서가 없을 때 납니다. 이 브리지는 시작할 때 `certs/`에 자동 생성합니다. `certs/`를 지우고 다시 시작해 보세요. |
| Browse를 눌러도 가운데 목록이 비어 있음 | 노드 필터 문제입니다. 3-3의 필터(`;s=`)와 Limit 256을 확인하세요. |
| 연결은 되는데 센서·액추에이터가 안 움직임 | 드래그 방향을 확인하세요(3-3의 5번). 래더가 시뮬레이터에 써져 있고 시뮬레이션이 실행 중인지도 확인하세요. |
| 클라이언트가 노드에 쓰려는데 거부됨 | `from_plc` 노드는 읽기 전용입니다. 방향이 `to_plc`인 주소에 연결했는지 확인하세요. |

디버그 로그가 필요하면 환경변수 `BRIDGE_DEBUG=1`을 켜고 실행합니다(OPC UA 요청이 모두 출력되어 매우 시끄럽습니다).

---

## 6. 테스트

```
.venv\Scripts\python.exe test_bridge.py
```

모의 PLC만 쓰므로 MX Component나 GX Simulator2가 없어도 됩니다. 잘못된 설정 거부, 센서→모터 200ms 이내 반영, 음수 워드, 읽기 전용 노드, 호출 횟수, 값 변화 시에만 쓰기와 PLC 리셋 복구, PLC 2초 단절 후 재연결을 확인합니다.

---

## 7. 파일 구성

| 파일 | 내용 |
|---|---|
| `bridge.py` | 설정 검증, MX/모의 PLC 드라이버, OPC UA 서버, 동기화 루프 |
| `tags.yaml` | PLC 연결 설정과 태그 목록 |
| `test_bridge.py` | 모의 PLC 테스트 |
| `setup.bat` | 32비트 Python 환경 설치 |
| `start.bat` | 서버 실행 |
| `requirements.txt` | Python 패키지 |

---

## 8. 확인하지 못한 것

- 이 개발 PC 한 대에서만 시험했습니다. 다른 PC에서의 설치·실행, 두 PC 구성, 인터넷이 없는 설치는 확인하지 못했습니다.
- Factory I/O 화면에서 컨베이어 데모(센서 → 컨베이어)가 동작하는 것은 확인했습니다. 61개 전체 태그를 Factory I/O 화면에서 연결해 본 것은 아닙니다.
- MX Component의 정확한 버전, 그리고 다른 CPU 종류에서의 동작은 확인하지 못했습니다.
- 라이선스 파일은 아직 없습니다. 공개 저장소로 쓰려면 라이선스를 정해야 합니다.

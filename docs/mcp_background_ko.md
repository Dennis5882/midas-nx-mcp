# MCP 배경 정리 (2026-11-02 대화 기록)

MIDAS-API-NX-SDK 작업 중 "MCP가 필요한가?"에서 시작해 나눈 이야기를 정리한 문서.
이 프로젝트를 왜, 어떤 방식으로 만드는지의 근거다.

> 날짜가 붙은 사실(플랜별 지원 여부, Codex 기본 설정 등)은 바뀔 수 있다.
> "확인 필요"라고 적힌 것은 실제 화면에서 확인할 것.

---

## 1. 셸(shell)이란

컴퓨터에 **글자로 명령을 내리는 창**. 윈도우의 명령 프롬프트(cmd), PowerShell이 셸이다.

```
python scripts/live_crud_check.py --product gen --tier extras10 --save-dir C:/temp
```

이런 한 줄을 실행하는 곳. VS Code 안의 Claude Code는 이 셸을 직접 쓸 수 있어서,
SDK 스크립트를 써서 실행하고, 테스트·빌드·git·릴리즈까지 대화 중에 할 수 있다.

## 2. MCP의 원리

> **MCP는 AI에게 "이런 도구들이 있어, 필요하면 이렇게 불러"라고 알려 주는 표준 규칙이다.**

### 비유: 식당

- **AI(Claude)** = 손님. 말은 잘하지만 주방(내 PC, NX)에 직접 못 들어감
- **MCP 서버** = 메뉴판 + 종업원
  - 메뉴판: 할 수 있는 일(도구) 목록과 각 도구에 필요한 값
  - 종업원: 주문을 받아 주방에 전달하고 결과를 가져옴
- **MCP** = 주문서 양식. 메뉴 보여 주는 법, 주문하는 법, 결과 돌려주는 법이 표준이라
  어느 손님(AI 앱)이든 어느 식당(서버)이든 똑같이 주문할 수 있다

### 실제 흐름

1. Claude 데스크톱이 켜질 때 내 PC의 MCP 서버를 실행
2. Claude가 "뭐 할 수 있어?" → 서버가 도구 목록 전달 (예: `get_nodes()`)
3. 사용자가 "Gen 모델에 절점 몇 개야?" → Claude가 `get_nodes` 호출을 요청
4. MCP 서버가 `midas-nx` SDK로 `GET /db/NODE` → MIDASIT 중계 서버 → NX가 응답
5. 서버가 결과를 돌려주면 Claude가 "절점 22개입니다"라고 답함

### 왜 표준이 필요했나

예전에는 AI 앱마다 도구 붙이는 방식이 달라 앱마다 따로 만들어야 했다.
MCP는 이것을 **USB-C처럼** 통일했다. 서버 하나를 만들면 MCP를 지원하는 어떤 AI 앱에도 꽂힌다.

## 3. 언제 MCP가 필요한가

**필요 여부는 "AI가 내 PC에서 명령을 실행할 수 있느냐"로 갈린다.**

| 환경 | 어디서 실행되나 | NX에 닿는 방법 | MCP |
|---|---|---|---|
| VS Code + Claude Code | 내 PC, 셸 사용 | SDK 스크립트를 직접 실행 | 불필요 (붙일 수는 있음) |
| Claude 데스크톱 앱 안의 Claude Code | 내 PC, 셸 사용 | 위와 같음 | 불필요 |
| Codex CLI / IDE 확장 / Codex 앱 | 내 PC, 샌드박스 안에서 명령 실행 | SDK 스크립트 직접 실행 | 불필요. 단, 샌드박스 네트워크 허용 필요 (아래) |
| Claude 데스크톱 앱 **일반 대화창** | 내 PC, 셸 없음 | **로컬 MCP 서버** | 필요 (이 프로젝트) |
| claude.ai **웹** | Anthropic 클라우드 | **원격 MCP 서버** + 커스텀 커넥터 | 필요 |
| Codex 클라우드(웹) | OpenAI 클라우드 샌드박스 | 원격 MCP 서버 | 필요 |

정리:
- 지금 쓰는 **VS Code + Claude Code + midas-nx SDK + Gen/Civil NX**는 거의 뭐든 직접 하므로 MCP가 필요 없다.
- **앱(데스크톱)** 은 갇혀 있다기보다 셸이 없는 것이라 **로컬 MCP**로 해결된다.
- 정말 갇혀 있는 것은 **웹/클라우드**이고, 이때는 인터넷에 올린 **원격 MCP**가 필요하다.

### Codex 주의점

Codex는 보안 샌드박스에서 명령을 실행하며, 기본 설정에서 **인터넷 접속이 막혀 있을 수 있다**.
MIDAS API는 MIDASIT 중계 서버(`moa-engineers.midasit.com`)를 거치므로 네트워크가 막히면
SDK 호출이 실패한다. Codex 설정에서 네트워크 접근을 허용해야 한다. (확인 필요)

### 덧붙임: MIDAS API는 원래 인터넷 API

요청은 MIDASIT 중계 서버를 거쳐 NX에 닿는다. 그래서 이론상 인터넷이 되는 샌드박스에서
MAPI 키만 있으면 MCP 없이도 호출 가능하다. 하지만 채팅에 키를 붙여 넣는 것은 보안상
나쁘고, 웹 샌드박스는 외부 접속이 막힌 경우가 많아 실무적으로는 MCP가 맞다.

## 4. 로컬 MCP vs 원격 MCP

| | 로컬 MCP (이 프로젝트) | 원격 MCP |
|---|---|---|
| 어디서 도나 | 내 PC. 클라이언트 앱이 직접 실행 (stdio) | 인터넷 서버 |
| 비용 | 무료, 호스팅 없음 | 무료 플랜 있음 (예: Cloudflare Workers), 운영 부담 |
| 쓸 수 있는 곳 | 내 PC의 데스크톱 앱 | claude.ai 웹, 다른 사람 |
| MAPI 키 | 내 PC를 벗어나지 않음 | 서버에 보관해야 함 |
| 보안 | 단순 | 인증을 직접 걸어야 함 (남이 내 NX를 조작하지 못하게) |

개인이 무료로 쓰는 현실적인 순서:
1. 지금처럼 VS Code + Claude Code (이미 다 됨)
2. Claude 데스크톱 앱 + **로컬 MCP** (대화창에서 NX를 다루고 싶을 때) ← 이 프로젝트
3. 웹 + 원격 MCP (꼭 필요할 때만, 보안까지 직접 책임)

**확인 필요:** 사용 중인 Claude 플랜에서 데스크톱 앱의 로컬 MCP(Settings > Developer),
claude.ai의 커스텀 커넥터를 쓸 수 있는지.

## 5. 언어는 상관없다

- MCP는 **언어와 무관한 통신 규약**(JSON-RPC 메시지). 클라이언트는 서버가 무슨 언어인지 모르고 신경 쓰지 않는다.
- 언어가 의미 있는 것은 두 가지뿐:
  1. 서버를 **만들고 고치는 사람**이 쓰기 편한 언어
  2. 서버를 **실행하는 PC**에 그 언어의 실행 환경(Python 또는 Node.js)이 있어야 함
- MCP **공식 SDK는 여러 언어**로 있다: TypeScript, Python, Java, Kotlin, C#, Go 등.
  TypeScript SDK가 가장 먼저 나와 기준 구현이었고 규격이 TypeScript 타입으로 정의돼 있지만,
  Python SDK(`mcp` 패키지, FastMCP 포함)도 공식이고 충분히 성숙하다.
  (최신 목록은 modelcontextprotocol.io에서 확인)

### midas-nx 패키지와의 관계

MCP 서버 하나는 보통 한 언어로 만들고 그 언어의 `midas-nx` 하나만 쓴다.

| 서버 언어 | 쓰는 패키지 | 설치 |
|---|---|---|
| Python | PyPI `midas-nx` | `pip install midas-nx mcp` |
| TypeScript | npm `midas-nx` | `npm install midas-nx @modelcontextprotocol/sdk` |

두 패키지는 같은 계약(`contracts/`)에서 나와 엔드포인트·안전장치·오류 처리가 같으므로
어느 쪽이든 NX로 할 수 있는 일은 같다. 이 프로젝트는 **Python**으로 한다
(FastMCP가 간단하고, 이 PC에 Python 환경과 키 설정이 이미 있음).

## 6. 왜 별도 폴더/프로젝트인가

1. **역할이 다르다.** MCP 서버는 SDK를 *가져다 쓰는 앱*이다. smart-midas-steel,
   Steel-Frame과 같은 위치.
2. **SDK 저장소를 깔끔하게.** SDK 쪽은 계약·CI·lockstep 버전 규칙이 촘촘하다.
3. **실제 사용자와 같은 조건.** PyPI에 올라간 `midas-nx`를 `pip install`로 받아 쓰므로
   공개 패키지가 실제로 동작하는지 확인하는 셈이다.
4. **버전 독립.** MCP를 고칠 때마다 SDK를 릴리즈할 필요가 없다.

나중에 공개한다면 PyPI에 `midas-nx-mcp`로 올리고, 받는 사람은 설치 시
`midas-nx`가 의존성으로 함께 깔린다.

## 7. 이 프로젝트의 방향 (요약, 자세한 것은 PLAN.md / AGENTS.md)

- **1단계: 읽기 전용.** 연결 확인, 테이블 목록·스키마·조회, 모델 요약, 결과 테이블
  (`/post/TABLE`, `EXPORT_PATH` 없이)
- **2단계: 조심스러운 쓰기.** `C:/temp`로만 저장, 몇 가지 레코드 생성/수정, id 지정 삭제만.
  쓰기 후 반드시 다시 읽어 확인
- **절대 도구로 열지 않는 것:** `/doc/NEW`, `/doc/OPEN`, `/doc/IMPORT*`,
  테이블 통째 삭제(`DELETE /db/<NAME>`, `delete_all()`)
